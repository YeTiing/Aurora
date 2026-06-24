# -*- coding: utf-8 -*-
"""LSP Client — subprocess + JSON-RPC 2.0 over stdio.

Port of cc-haha's src/services/lsp/LSPClient.ts.
Spawns LSP server process, manages JSON-RPC communication, handles lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from asyncio import StreamReader, StreamWriter
from asyncio.subprocess import Process
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("aurora.lsp.client")

# ── JSON-RPC 2.0 types ───────────────────────────────────────────

@dataclass
class RPCRequest:
    jsonrpc: str = "2.0"
    id: str | int = ""
    method: str = ""
    params: Any = None

@dataclass
class RPCResponse:
    jsonrpc: str = "2.0"
    id: str | int = ""
    result: Any = None
    error: dict | None = None

@dataclass
class RPCNotification:
    jsonrpc: str = "2.0"
    method: str = ""
    params: Any = None


class LSPClient:
    """JSON-RPC 2.0 client for LSP server communication over stdio."""

    def __init__(self, server_name: str, on_crash: Callable[[Exception], None] | None = None):
        self.server_name = server_name
        self._on_crash = on_crash
        self._process: Process | None = None
        self._reader: StreamReader | None = None
        self._writer: StreamWriter | None = None
        self._capabilities: dict = {}
        self._is_initialized = False
        self._start_failed = False
        self._start_error: Exception | None = None
        self._is_stopping = False
        self._request_id = 0
        self._pending_requests: dict[str | int, asyncio.Future] = {}
        self._notification_handlers: dict[str, list[Callable]] = {}
        self._request_handlers: dict[str, Callable] = {}
        self._reader_task: asyncio.Task | None = None
        # Queue handlers registered before connection ready
        self._pending_notif_handlers: dict[str, list[Callable]] = {}
        self._pending_req_handlers: dict[str, Callable] = {}

    # ── Properties ─────────────────────────────────────────────

    @property
    def capabilities(self) -> dict:
        return self._capabilities

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self, command: str, args: list[str],
                    env: dict | None = None, cwd: str | None = None) -> None:
        """Spawn LSP server process and set up JSON-RPC I/O."""
        try:
            env_full = os.environ.copy()
            if env:
                env_full.update(env)
            creationflags = 0
            if os.name == "nt":
                creationflags = 0x08000000  # CREATE_NO_WINDOW

            self._process = await asyncio.create_subprocess_exec(
                command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_full,
                cwd=cwd,
                creationflags=creationflags if os.name == "nt" else 0,
            )

            self._reader = self._process.stdout
            self._writer = self._process.stdin

            # Start stderr reader
            asyncio.create_task(self._read_stderr())

            # Start JSON-RPC message reader
            self._reader_task = asyncio.create_task(self._read_messages())

            # Handle process exit
            asyncio.create_task(self._watch_process())

        except Exception as e:
            self._start_failed = True
            self._start_error = e
            logger.error(f"LSP server '{self.server_name}' failed to start: {e}")
            raise

    async def initialize(self, params: dict) -> dict:
        """Send initialize request, set capabilities."""
        self._check_start_failed()
        result = await self.send_request("initialize", params)
        self._capabilities = result.get("capabilities", {})
        self._is_initialized = True
        await self.send_notification("initialized", {})
        return result

    async def stop(self) -> None:
        """Gracefully shutdown LSP server."""
        self._is_stopping = True
        try:
            if self._is_initialized:
                await self.send_request("shutdown", {})
                await self.send_notification("exit", {})
        except Exception:
            pass

        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass

        self._is_initialized = False
        self._pending_requests.clear()

    # ── Request / Notification ──────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def send_request(self, method: str, params: Any) -> Any:
        """Send JSON-RPC request and await response."""
        self._check_start_failed()
        rid = self._next_id()
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_requests[rid] = future

        msg = RPCRequest(id=rid, method=method, params=params)
        await self._write_message(msg)

        try:
            result = await asyncio.wait_for(future, timeout=60)
            if isinstance(result, dict) and "error" in result:
                err = result["error"]
                raise RuntimeError(f"LSP error {err.get('code')}: {err.get('message')}")
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(rid, None)
            raise TimeoutError(f"LSP request '{method}' timed out")
        finally:
            self._pending_requests.pop(rid, None)

    async def send_notification(self, method: str, params: Any) -> None:
        """Send JSON-RPC notification (no response expected)."""
        self._check_start_failed()
        msg = RPCNotification(method=method, params=params)
        await self._write_message(msg)

    def on_notification(self, method: str, handler: Callable) -> None:
        """Register handler for server→client notifications."""
        if self._reader_task is None:
            self._pending_notif_handlers.setdefault(method, []).append(handler)
        else:
            self._notification_handlers.setdefault(method, []).append(handler)

    def on_request(self, method: str, handler: Callable) -> None:
        """Register handler for server→client requests."""
        if self._reader_task is None:
            self._pending_req_handlers[method] = handler
        else:
            self._request_handlers[method] = handler

    # ── Internal I/O ────────────────────────────────────────────

    async def _write_message(self, msg: RPCRequest | RPCNotification) -> None:
        data = {
            "jsonrpc": msg.jsonrpc,
            "method": msg.method,
            "params": msg.params,
        }
        if isinstance(msg, RPCRequest):
            data["id"] = msg.id

        body = json.dumps(data, default=str, ensure_ascii=False)
        header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
        self._writer.write((header + body).encode("utf-8"))
        await self._writer.drain()

    async def _read_message(self) -> dict | None:
        """Read one JSON-RPC message from stdout (LSP header+body format)."""
        # Read headers
        headers = {}
        while True:
            line = await self._reader.readline()
            line = line.decode("utf-8").rstrip("\r\n")
            if not line:
                break
            if ":" in line:
                key, val = line.split(":", 1)
                headers[key.strip().lower()] = val.strip()

        content_length = int(headers.get("content-length", 0))
        if content_length == 0:
            return None

        body = await self._reader.readexactly(content_length)
        return json.loads(body.decode("utf-8"))

    async def _read_messages(self) -> None:
        """Main read loop: parse JSON-RPC messages from server stdout."""
        # Flush pending handlers
        for method, handlers in self._pending_notif_handlers.items():
            for h in handlers:
                self._notification_handlers.setdefault(method, []).append(h)
        self._pending_notif_handlers.clear()
        for method, handler in self._pending_req_handlers.items():
            self._request_handlers[method] = handler
        self._pending_req_handlers.clear()

        while True:
            try:
                msg = await self._read_message()
                if msg is None:
                    break

                if "id" in msg and "method" not in msg:
                    # Response
                    rid = msg["id"]
                    fut = self._pending_requests.get(rid)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_result({"error": msg["error"]})
                        else:
                            fut.set_result(msg.get("result"))
                elif "id" in msg and "method" in msg:
                    # Server→client request
                    handlers = self._request_handlers.get(msg["method"], [])
                    handler = handlers if callable(handlers) else (handlers[0] if handlers else None)
                    if handler:
                        result = handler(msg.get("params"))
                        if asyncio.iscoroutine(result):
                            result = await result
                        resp = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
                        body = json.dumps(resp, default=str)
                        header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
                        self._writer.write((header + body).encode("utf-8"))
                        await self._writer.drain()
                elif "method" in msg:
                    # Notification
                    for handler in self._notification_handlers.get(msg["method"], []):
                        try:
                            res = handler(msg.get("params"))
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as e:
                            logger.debug(f"LSP notification handler error for '{msg['method']}': {e}")

            except asyncio.IncompleteReadError:
                break
            except Exception as e:
                if not self._is_stopping:
                    logger.error(f"LSP read error for '{self.server_name}': {e}")
                break

    async def _read_stderr(self) -> None:
        """Read and log stderr from LSP server."""
        try:
            while self._process and self._process.stderr:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug(f"[LSP:{self.server_name}] {text}")
        except Exception:
            pass

    async def _watch_process(self) -> None:
        """Monitor process exit, propagate crash."""
        if not self._process:
            return
        try:
            code = await self._process.wait()
            if code != 0 and not self._is_stopping:
                self._is_initialized = False
                self._start_failed = False
                err = RuntimeError(f"LSP server '{self.server_name}' crashed with exit code {code}")
                logger.error(str(err))
                if self._on_crash:
                    self._on_crash(err)
        except Exception:
            pass

    def _check_start_failed(self) -> None:
        if self._start_failed:
            raise self._start_error or RuntimeError(f"LSP server '{self.server_name}' failed to start")


def create_lsp_client(server_name: str, on_crash: Callable[[Exception], None] | None = None) -> LSPClient:
    """Factory: create a new LSP client."""
    return LSPClient(server_name, on_crash)
