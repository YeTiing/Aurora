# -*- coding: utf-8 -*-
"""Phase 0 probe: drive pyright-langserver over stdio and dump raw JSON.

EXEMPT from the 300-line module limit: this is a one-off Phase 0 探明脚本
（INDEX.md §Phase 0 明确要求「写一个一次性探测脚本，不要放进正式模块结构」），
不是产品代码。保留它是因为它的输出（同目录的 12 份 JSON + FINDINGS.md）
是采点规则的唯一实证依据。

Throwaway-quality. NOT a production module.

Key point (per INDEX.md 2.2):
  Aurora passes rootUri=None / workspaceFolders=[] -> pyright can't find the
  project root and cross-file resolution silently degrades. This probe passes a
  correct rootUri + populated workspaceFolders, so the observed results are
  trustworthy for cross-file analysis.

Transport (JSON-RPC 2.0 + Content-Length framing + stdio) is ported from
D:\\codex_Projects\\Aurora\\backend\\lsp\\client.py (not rewritten).
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent

# ── Target repo / file ────────────────────────────────────────────────────
# backend/tools/base.py::safe_resolve_path is a top-level function with
# cross-file callers (file_rw.py, apply_patch.py, list_files.py, view_image.py)
# all in the same `backend.tools` package -> cross-file callHierarchy must work.
REPO_ROOT = Path(r"D:\codex_Projects\Aurora").resolve()
TARGET_REL = "backend/tools/base.py"
TARGET_FILE = REPO_ROOT / "backend" / "tools" / "base.py"
TARGET_SYMBOL = "safe_resolve_path"

# Abstract-method target for textDocument/implementation.
IMPL_REL = "backend/agent/llm_providers.py"
IMPL_FILE = REPO_ROOT / "backend" / "agent" / "llm_providers.py"
IMPL_SYMBOL = "chat_stream"

# Node entrypoint (bypasses the .cmd shim so asyncio can spawn it directly).
NODE_EXE = r"D:\NodeJS\node.exe"
LANGSERVER_JS = r"C:\home\zenos\.npm-global\node_modules\pyright\langserver.index.js"


def _hard_kill(pid: int) -> None:
    """Belt-and-braces kill of the pyright node process tree."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ── Minimal port of Aurora's LSPClient (framing only) ─────────────────────
class LSPClient:
    def __init__(self, name: str):
        self.name = name
        self._process: asyncio.subprocess.Process | None = None
        self._reader = None
        self._writer = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._notif_handlers: dict[str, list] = {}
        self._req_handlers: dict[str, object] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self.capabilities: dict = {}
        self.initialized = False
        self._stopping = False
        self.stderr_tail: list[str] = []
        self.pid: int | None = None

    async def start(self, command: str, args: list[str]) -> None:
        env = os.environ.copy()
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        self._process = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(REPO_ROOT),
            creationflags=creationflags,
        )
        self.pid = self._process.pid
        self._reader = self._process.stdout
        self._writer = self._process.stdin
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._reader_task = asyncio.create_task(self._read_messages())

    async def _write_message(self, msg: dict) -> None:
        body = json.dumps(msg, default=str, ensure_ascii=False)
        header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
        self._writer.write((header + body).encode("utf-8"))
        await self._writer.drain()

    async def _read_message(self) -> dict | None:
        headers: dict[str, str] = {}
        while True:
            raw = await self._reader.readline()
            if not raw:
                return None
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", 0))
        if n == 0:
            return None
        body = await self._reader.readexactly(n)
        return json.loads(body.decode("utf-8"))

    async def _read_messages(self) -> None:
        while True:
            try:
                msg = await self._read_message()
                if msg is None:
                    break
                if "id" in msg and "method" not in msg:
                    fut = self._pending.get(msg["id"])
                    if fut and not fut.done():
                        fut.set_result(msg.get("result") if "error" not in msg else {"__error__": msg["error"]})
                elif "id" in msg and "method" in msg:
                    handler = self._req_handlers.get(msg["method"])
                    result = handler(msg.get("params")) if handler else None
                    if asyncio.iscoroutine(result):
                        result = await result
                    await self._write_message({"jsonrpc": "2.0", "id": msg["id"], "result": result})
                elif "method" in msg:
                    for h in self._notif_handlers.get(msg["method"], []):
                        try:
                            r = h(msg.get("params"))
                            if asyncio.iscoroutine(r):
                                await r
                        except Exception as e:
                            print(f"[warn] notif handler {msg['method']}: {e}", file=sys.stderr)
            except asyncio.IncompleteReadError:
                break
            except Exception as e:
                if not self._stopping:
                    print(f"[warn] read loop: {e}", file=sys.stderr)
                break

    async def _read_stderr(self) -> None:
        try:
            while self._process and self._process.stderr:
                raw = await self._process.stderr.readline()
                if not raw:
                    break
                self.stderr_tail.append(raw.decode("utf-8", errors="replace").rstrip())
                if len(self.stderr_tail) > 50:
                    self.stderr_tail.pop(0)
        except Exception:
            pass

    def on_notification(self, method: str, handler) -> None:
        self._notif_handlers.setdefault(method, []).append(handler)

    def on_request(self, method: str, handler) -> None:
        self._req_handlers[method] = handler

    async def send_request(self, method: str, params, timeout: float = 120.0):
        self._request_id += 1
        rid = self._request_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._write_message({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)
        if isinstance(res, dict) and "__error__" in res:
            raise RuntimeError(f"LSP error on {method}: {res['__error__']}")
        return res

    async def send_notification(self, method: str, params) -> None:
        await self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    async def initialize(self, params: dict) -> dict:
        res = await self.send_request("initialize", params)
        self.capabilities = res.get("capabilities", {})
        self.initialized = True
        await self.send_notification("initialized", {})
        return res

    async def stop(self) -> None:
        self._stopping = True
        if self._process is None:
            return
        try:
            if self.initialized:
                await asyncio.wait_for(self.send_request("shutdown", {}), timeout=5)
                await self.send_notification("exit", {})
        except Exception:
            pass
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass


def _dump(name: str, payload) -> None:
    path = HERE / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"    wrote {path.name}")


def _short(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


async def _try_request(client: LSPClient, method: str, params):
    """Return (result, None) or (None, error_dict) instead of raising.

    Needed because pyright returns JSON-RPC -32601 for methods it does not
    implement (e.g. textDocument/implementation). A documented negative result
    is the point of Phase 0.
    """
    try:
        return await client.send_request(method, params), None
    except RuntimeError as e:
        msg = str(e)
        marker = "LSP error on "
        detail = msg.split(marker, 1)[-1].split(": ", 1)[-1] if marker in msg else msg
        return None, {"method": method, "detail": detail}


async def run(client: LSPClient) -> None:
    # Server -> client requests that pyright sends; must answer or it stalls.
    client.on_request("client/registerCapability", lambda p: None)
    client.on_request("window/workDoneProgress/create", lambda p: None)
    client.on_request("workspace/configuration",
                      lambda p: [{}] * len((p or {}).get("items", [])))

    diagnostics: list[dict] = []
    client.on_notification("textDocument/publishDiagnostics",
                           lambda p: diagnostics.append(p))

    root_uri = REPO_ROOT.as_uri()
    file_uri = TARGET_FILE.as_uri()

    # ── (1) initialize — CORRECT rootUri + workspaceFolders ──────────────
    init_params = {
        "processId": os.getpid(),
        "clientInfo": {"name": "necessity-phase0-probe", "version": "0.1"},
        "rootUri": root_uri,
        "rootPath": str(REPO_ROOT),
        "workspaceFolders": [{"uri": root_uri, "name": REPO_ROOT.name}],
        "capabilities": {
            "textDocument": {
                "synchronization": {"dynamicRegistration": False},
                "publishDiagnostics": {"relatedInformation": True},
                "documentSymbol": {
                    "hierarchicalDocumentSymbolSupport": True,
                    "symbolKind": {"valueSet": list(range(1, 27))},
                },
                "callHierarchy": {"dynamicRegistration": False},
                "implementation": {"dynamicRegistration": False},
                "references": {"dynamicRegistration": False},
            },
            "workspace": {"workspaceFolders": True, "configuration": False},
            "general": {"positionEncodings": ["utf-16"]},
        },
    }
    init_result = await client.initialize(init_params)
    _dump("01_initialize.json", {
        "_probe_meta": {
            "rootUri_sent": root_uri,
            "workspaceFolders_sent": init_params["workspaceFolders"],
            "processId_sent": init_params["processId"],
            "callHierarchyProvider_truthy": bool(
                client.capabilities.get("callHierarchyProvider")
            ),
            "callHierarchyProvider_raw": client.capabilities.get("callHierarchyProvider"),
            "positionEncoding": init_result.get("capabilities", {}).get("positionEncoding"),
            "serverInfo": init_result.get("serverInfo"),
        },
        "raw_initialize_result": init_result,
    })
    print(f"[1] initialize ok; callHierarchyProvider={client.capabilities.get('callHierarchyProvider')!r}")

    # ── (2) didOpen -> diagnostics ───────────────────────────────────────
    text = TARGET_FILE.read_text(encoding="utf-8")
    await client.send_notification("textDocument/didOpen", {
        "textDocument": {"uri": file_uri, "languageId": "python", "version": 1, "text": text}
    })
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 20
    while loop.time() < deadline:
        if any(d.get("uri") == file_uri for d in diagnostics):
            break
        await asyncio.sleep(0.25)
    await asyncio.sleep(1.0)
    _dump("02_didopen_diagnostics.json", {
        "_probe_meta": {
            "fileUri": file_uri,
            "publishDiagnostics_notifications_received": len(diagnostics),
            "diagnostics_for_target": [d for d in diagnostics if d.get("uri") == file_uri],
        },
        "raw_notifications": diagnostics,
    })
    n_diag = sum(len(d.get("diagnostics", [])) for d in diagnostics if d.get("uri") == file_uri)
    print(f"[2] didOpen -> {len(diagnostics)} publishDiagnostics, {n_diag} diagnostics on target")

    # ── (3) documentSymbol ───────────────────────────────────────────────
    symbols = await client.send_request("textDocument/documentSymbol", {
        "textDocument": {"uri": file_uri}
    })
    _dump("03_document_symbol.json", {
        "_probe_meta": {
            "fileUri": file_uri,
            "count": len(symbols or []),
            "symbol_names": [s.get("name") for s in (symbols or [])],
            "top_level_keys": sorted((symbols or [{}])[0].keys()) if symbols else [],
        },
        "raw_result": symbols,
    })
    print(f"[3] documentSymbol -> {len(symbols or [])} symbols: {[s.get('name') for s in (symbols or [])]}")

    target_sym = None
    for s in (symbols or []):
        if s.get("name") == TARGET_SYMBOL:
            target_sym = s
            break
    if target_sym is None:
        raise SystemExit(f"target symbol {TARGET_SYMBOL!r} not found in documentSymbol output")
    range_start = target_sym["range"]["start"]
    sel_start = target_sym["selectionRange"]["start"]
    print(f"[3] target={TARGET_SYMBOL} range.start={range_start} selectionRange.start={sel_start}")

    def refs_params(pos: dict) -> dict:
        return {
            "textDocument": {"uri": file_uri},
            "position": pos,
            "context": {"includeDeclaration": False},
        }

    # ── (4a/b) references: range.start vs selectionRange.start ───────────
    refs_by_range = await client.send_request("textDocument/references", refs_params(range_start))
    refs_by_sel = await client.send_request("textDocument/references", refs_params(sel_start))
    _dump("04a_refs_by_range.json", {
        "_probe_meta": {
            "position_used": range_start,
            "position_kind": "range.start",
            "includeDeclaration": False,
            "count": len(refs_by_range or []),
            "files": sorted({_short(r.get("uri", "")) for r in (refs_by_range or [])}),
        },
        "raw_result": refs_by_range,
    })
    _dump("04b_refs_by_selection.json", {
        "_probe_meta": {
            "position_used": sel_start,
            "position_kind": "selectionRange.start",
            "includeDeclaration": False,
            "count": len(refs_by_sel or []),
            "files": sorted({_short(r.get("uri", "")) for r in (refs_by_sel or [])}),
        },
        "raw_result": refs_by_sel,
    })
    print(f"[4] references: range.start={len(refs_by_range or [])} "
          f"selectionRange.start={len(refs_by_sel or [])}")
    print(f"    selection refs files={sorted({_short(r.get('uri','')) for r in (refs_by_sel or [])})}")

    # ── (4c) references with includeDeclaration=true (self-loop check) ───
    refs_incl = await client.send_request("textDocument/references", {
        "textDocument": {"uri": file_uri},
        "position": sel_start,
        "context": {"includeDeclaration": True},
    })
    same_file = [r for r in (refs_incl or []) if _short(r.get("uri", "")) == TARGET_FILE.name]
    decl_hits = [r for r in same_file if r.get("range", {}).get("start") == sel_start]
    _dump("04c_refs_include_declaration.json", {
        "_probe_meta": {
            "position_used": sel_start,
            "includeDeclaration": True,
            "count": len(refs_incl or []),
            "delta_vs_false": len(refs_incl or []) - len(refs_by_sel or []),
            "declaration_entry_present_at_selectionRange_start": len(decl_hits),
            "same_file_refs": [r.get("range", {}).get("start") for r in same_file],
            "files": sorted({_short(r.get("uri", "")) for r in (refs_incl or [])}),
        },
        "raw_result": refs_incl,
    })
    print(f"[4c] includeDeclaration=true -> {len(refs_incl or [])} "
          f"(delta vs false = {len(refs_incl or []) - len(refs_by_sel or [])}); "
          f"decl entry present={len(decl_hits)}")

    # ── (5) textDocument/prepareCallHierarchy (NOT callHierarchy/...) ────
    prep = await client.send_request("textDocument/prepareCallHierarchy", {
        "textDocument": {"uri": file_uri},
        "position": sel_start,
    })
    _dump("05_prepare_call_hierarchy.json", {
        "_probe_meta": {
            "method": "textDocument/prepareCallHierarchy",
            "position_used": sel_start,
            "position_kind": "selectionRange.start",
            "count": len(prep or []),
            "item_keys": sorted((prep or [{}])[0].keys()) if prep else [],
        },
        "raw_result": prep,
    })
    print(f"[5] prepareCallHierarchy -> {len(prep or [])} item(s)")

    incoming = None
    if prep:
        incoming = await client.send_request("callHierarchy/incomingCalls", {"item": prep[0]})
        _dump("06_incoming_calls.json", {
            "_probe_meta": {
                "count": len(incoming or []),
                "caller_names": [(c.get("from") or {}).get("name") for c in (incoming or [])],
                "from_keys": sorted(((incoming or [{}])[0].get("from") or {}).keys()) if incoming else [],
                "has_fromRanges": bool((incoming or [{}])[0].get("fromRanges")) if incoming else False,
                "fromRanges_len": len((incoming or [{}])[0].get("fromRanges", [])) if incoming else 0,
                "edge_keys": sorted((incoming or [{}])[0].keys()) if incoming else [],
            },
            "raw_result": incoming,
        })
        print(f"[6] incomingCalls -> {len(incoming or [])}: "
              f"{[(c.get('from') or {}).get('name') for c in (incoming or [])]}")
        _dump("06b_outgoing_calls.json", {
            "_probe_meta": {"count": len(await client.send_request(
                "callHierarchy/outgoingCalls", {"item": prep[0]}) or [])},
            "raw_result": await client.send_request("callHierarchy/outgoingCalls", {"item": prep[0]}),
        })

    # ── (7) implementation on the plain function ─────────────────────────
    impl, impl_err = await _try_request(client, "textDocument/implementation", {
        "textDocument": {"uri": file_uri},
        "position": sel_start,
    })
    _dump("07_implementation.json", {
        "_probe_meta": {
            "target": f"{TARGET_REL}::{TARGET_SYMBOL} (plain module-level function)",
            "position_used": sel_start,
            "server_error": impl_err,
            "result_type": type(impl).__name__,
            "count": len(impl or []) if isinstance(impl, list) else None,
        },
        "raw_result": impl,
    })
    n7 = (len(impl) if isinstance(impl, list) else "non-list") if impl_err is None else f"ERROR {impl_err}"
    print(f"[7] implementation(plain function) -> {n7}")

    # ── (8) implementation on an abstract method (inheritance test) ──────
    impl_uri = IMPL_FILE.as_uri()
    impl_text = IMPL_FILE.read_text(encoding="utf-8")
    await client.send_notification("textDocument/didOpen", {
        "textDocument": {"uri": impl_uri, "languageId": "python", "version": 1, "text": impl_text}
    })
    impl_syms = await client.send_request("textDocument/documentSymbol", {
        "textDocument": {"uri": impl_uri}
    })
    # BaseProvider is a Class symbol; its children are methods.
    base_cls = next((s for s in (impl_syms or []) if s.get("name") == "BaseProvider"), None)
    method_start = None
    method_sym = None
    if base_cls:
        for ch in base_cls.get("children", []):
            if ch.get("name") == IMPL_SYMBOL:
                method_sym = ch
                method_start = ch["selectionRange"]["start"]
                break
    await asyncio.sleep(2.0)
    impl2 = None
    impl2_err = None
    if method_start is not None:
        impl2, impl2_err = await _try_request(client, "textDocument/implementation", {
            "textDocument": {"uri": impl_uri},
            "position": method_start,
        })
    _dump("08_implementation_method.json", {
        "_probe_meta": {
            "target": f"{IMPL_REL}::BaseProvider.{IMPL_SYMBOL} (abstract method)",
            "method_found": method_sym is not None,
            "position_used": method_start,
            "selectionRange_of_method": method_sym.get("selectionRange") if method_sym else None,
            "range_of_method": method_sym.get("range") if method_sym else None,
            "server_error": impl2_err,
            "result_type": type(impl2).__name__,
            "count": len(impl2 or []) if isinstance(impl2, list) else None,
        },
        "raw_result": impl2,
    })
    n8 = (len(impl2) if isinstance(impl2, list) else "non-list") if impl2_err is None else f"ERROR {impl2_err}"
    print(f"[8] implementation(abstract method) -> {n8}")

    # ── (9) prepareCallHierarchy on a method (nested symbol) ─────────────
    if method_start is not None:
        prep_m = await client.send_request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": impl_uri},
            "position": method_start,
        })
        inc_m = await client.send_request("callHierarchy/incomingCalls", {"item": prep_m[0]}) if prep_m else None
        _dump("09_method_call_hierarchy.json", {
            "_probe_meta": {
                "target": f"BaseProvider.{IMPL_SYMBOL}",
                "prepare_count": len(prep_m or []),
                "incoming_count": len(inc_m or []),
                "incoming_from": [(c.get("from") or {}).get("name") for c in (inc_m or [])],
            },
            "prepare_raw": prep_m,
            "incoming_raw": inc_m,
        })
        print(f"[9] method callHierarchy -> prep={len(prep_m or [])} incoming={len(inc_m or [])}")


def main() -> int:
    client = LSPClient("pyright")
    pid_holder: dict[str, int | None] = {"pid": None}
    try:
        client.start_sync_pid = None  # noqa
        asyncio.run(_bootstrap_and_run(client, pid_holder))
    except KeyboardInterrupt:
        print("[!] interrupted")
        return 130
    finally:
        if client.pid:
            _hard_kill(client.pid)
        # Sweep only pyright langserver nodes (matched by command line) so we
        # never kill unrelated node apps.
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='node.exe'", "get",
                 "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            for line in out.splitlines():
                if "langserver.index.js" not in line:
                    continue
                tail = line.strip().split()
                if tail and tail[-1].isdigit():
                    _hard_kill(int(tail[-1]))
        except Exception:
            pass
    return 0


async def _bootstrap_and_run(client: LSPClient, pid_holder) -> None:
    atexit.register(lambda: _hard_kill(client.pid) if client.pid else None)
    await client.start(NODE_EXE, [LANGSERVER_JS, "--stdio"])
    pid_holder["pid"] = client.pid
    print(f"[*] pyright langserver pid={client.pid}")
    try:
        await run(client)
    finally:
        try:
            await asyncio.wait_for(client.stop(), timeout=10)
            print("[*] pyright stopped via shutdown/exit")
        except Exception as e:
            print(f"[warn] graceful stop failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
