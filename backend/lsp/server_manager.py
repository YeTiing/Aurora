# -*- coding: utf-8 -*-
"""LSP Server Manager — multi-server orchestration & file routing.

Port of cc-haha's src/services/lsp/LSPServerManager.ts.
Manages multiple LSP server instances, routes requests by file extension.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from .config import BUILTIN_CONFIGS, find_available_servers, get_config_for_file
from .server_instance import LSPServerInstance, LspServerState
from .client import create_lsp_client as _client_factory  # noqa

logger = logging.getLogger("aurora.lsp.manager")


class LSPServerManager:
    """Manages multiple LSP server instances. Routes requests by file extension."""

    def __init__(self, auto_start: bool = True):
        self._servers: dict[str, LSPServerInstance] = {}
        self._extension_map: dict[str, list[str]] = {}
        self._opened_files: dict[str, str] = {}  # URI → server name
        self._initialization_state: str = "not-started"  # not-started|pending|success|failed
        self._global_lock = asyncio.Lock()
        self._initialized_event = asyncio.Event()

    @property
    def state(self) -> str:
        return self._initialization_state

    @property
    def is_ready(self) -> bool:
        return self._initialization_state == "success"

    # ── Initialization ──────────────────────────────────────────

    async def initialize(self, server_names: list[str] | None = None,
                         root_path: str | os.PathLike | None = None) -> None:
        """Initialize all available LSP servers (or specific ones).

        `root_path` 是**仓库根目录**，会作为 rootUri/workspaceFolders 传给
        server。省略时退回各 config 的 cwd（兼容旧调用），但那会让
        pyright 失去项目根、跨文件解析静默退化 —— 新代码应显式传。
        """
        if self._initialization_state == "pending":
            return
        if self._initialization_state == "success":
            return  # Already initialized

        self._initialization_state = "pending"
        self._root_path = root_path

        # Discover available servers
        available = find_available_servers()
        if server_names:
            available = {k: v for k, v in available.items() if k in server_names}

        logger.info(f"LSP manager initializing {len(available)} servers: {list(available.keys())}")

        # Build extension map
        for server_name, config in available.items():
            for ext in config.extension_to_language:
                normalized = ext.lower()
                self._extension_map.setdefault(normalized, []).append(server_name)

        # Start servers in parallel
        tasks = []
        for server_name, config in available.items():
            instance = LSPServerInstance(server_name, config)
            self._servers[server_name] = instance

            # Register workspace/configuration handler
            instance.on_request("workspace/configuration",
                lambda params, s=server_name: self._handle_config_request(params, s))

            tasks.append(self._start_server_safely(server_name, instance))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failures = [r for r in results if isinstance(r, Exception)]
            if failures and len(failures) == len(results):
                self._initialization_state = "failed"
                logger.error(f"All LSP servers failed to start: {failures}")
                return

        self._initialization_state = "success"
        self._initialized_event.set()
        running = sum(1 for s in self._servers.values() if s.is_healthy())
        logger.info(f"LSP manager initialized: {running}/{len(self._servers)} servers running")

    async def _start_server_safely(self, name: str, instance: LSPServerInstance) -> None:
        try:
            await instance.start(getattr(self, '_root_path', None))
        except Exception as e:
            logger.warning(f"LSP server '{name}' failed to start: {e}")

    async def shutdown(self) -> None:
        """Shutdown all running servers."""
        logger.info(f"LSP manager shutting down {len(self._servers)} servers")
        tasks = []
        for name, server in list(self._servers.items()):
            if server.state in (LspServerState.RUNNING, LspServerState.ERROR):
                tasks.append(self._stop_server_safely(name, server))

        await asyncio.gather(*tasks, return_exceptions=True)
        self._servers.clear()
        self._extension_map.clear()
        self._opened_files.clear()
        self._initialization_state = "not-started"
        self._initialized_event.clear()

    async def _stop_server_safely(self, name: str, instance: LSPServerInstance) -> None:
        try:
            await instance.stop()
        except Exception as e:
            logger.debug(f"Error stopping '{name}': {e}")

    # ── File Routing ────────────────────────────────────────────

    def get_server_for_file(self, filepath: str) -> Optional[LSPServerInstance]:
        """Get LSP server for a file path based on extension."""
        _, ext = os.path.splitext(filepath)
        ext = ext.lower()
        server_names = self._extension_map.get(ext, [])
        if not server_names:
            return None
        return self._servers.get(server_names[0])

    async def ensure_server_started(self, filepath: str) -> Optional[LSPServerInstance]:
        """Ensure the appropriate LSP server is running for a file."""
        server = self.get_server_for_file(filepath)
        if not server:
            return None
        if server.state in (LspServerState.STOPPED, LspServerState.ERROR):
            try:
                await server.restart()
            except Exception as e:
                logger.warning(f"Failed to restart LSP server for {filepath}: {e}")
                return None
        return server

    async def send_request(self, filepath: str, method: str, params: Any) -> Any | None:
        """Send an LSP request routed by file extension."""
        server = await self.ensure_server_started(filepath)
        if not server:
            return None
        try:
            return await server.send_request(method, params)
        except Exception as e:
            logger.debug(f"LSP request '{method}' failed for {filepath}: {e}")
            return None

    def get_all_servers(self) -> dict[str, LSPServerInstance]:
        """Return all server instances."""
        return dict(self._servers)

    # ── File Synchronization (didOpen/didChange/didSave/didClose) ──

    async def open_file(self, filepath: str, content: str) -> None:
        """Notify LSP server that a file was opened."""
        server = await self.ensure_server_started(filepath)
        if not server:
            return
        uri = self._path_to_uri(filepath)
        ext = os.path.splitext(filepath)[1].lower()
        lang_id = server.config.extension_to_language.get(ext, "")
        await server.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": lang_id,
                "version": 1,
                "text": content,
            },
        })
        self._opened_files[uri] = server.name

    async def change_file(self, filepath: str, content: str) -> None:
        """Notify LSP server that a file was changed."""
        server = self.get_server_for_file(filepath)
        if not server or not server.is_healthy():
            return
        uri = self._path_to_uri(filepath)
        # Full text sync
        await server.send_notification("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": int(time.time())},
            "contentChanges": [{"text": content}],
        })

    async def save_file(self, filepath: str) -> None:
        """Notify LSP server that a file was saved."""
        server = self.get_server_for_file(filepath)
        if not server or not server.is_healthy():
            return
        uri = self._path_to_uri(filepath)
        await server.send_notification("textDocument/didSave", {
            "textDocument": {"uri": uri},
        })

    async def close_file(self, filepath: str) -> None:
        """Notify LSP server that a file was closed."""
        uri = self._path_to_uri(filepath)
        server_name = self._opened_files.pop(uri, None)
        if not server_name:
            return
        server = self._servers.get(server_name)
        if server and server.is_healthy():
            await server.send_notification("textDocument/didClose", {
                "textDocument": {"uri": uri},
            })

    def is_file_open(self, filepath: str) -> bool:
        """Check if a file is already open on a compatible LSP server."""
        uri = self._path_to_uri(filepath)
        return uri in self._opened_files

    # ── Diagnostics ─────────────────────────────────────────────

    async def get_diagnostics(self, filepath: str) -> list[dict]:
        """Get diagnostics for a file (uses pull model if supported)."""
        server = await self.ensure_server_started(filepath)
        if not server:
            return []
        uri = self._path_to_uri(filepath)

        # Try pull model first
        caps = server.capabilities
        if caps.get("diagnosticProvider"):
            try:
                result = await server.send_request("textDocument/diagnostic", {
                    "textDocument": {"uri": uri},
                })
                return result.get("items", []) if result else []
            except Exception:
                pass
        return []

    async def get_hover(self, filepath: str, line: int, character: int) -> Optional[dict]:
        """Get hover information at a position."""
        server = await self.ensure_server_started(filepath)
        if not server:
            return None
        uri = self._path_to_uri(filepath)
        return await server.send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    async def get_definition(self, filepath: str, line: int, character: int) -> Optional[list[dict]]:
        """Get definition location(s) at a position."""
        server = await self.ensure_server_started(filepath)
        if not server:
            return None
        uri = self._path_to_uri(filepath)
        return await server.send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    # ── callHierarchy ───────────────────────────────────────────
    # 移植自 Necessity core/index/server_manager.py。三者都**忠实透传**
    # pyright 的返回结构，不 reshape —— 结果语义由调用方
    # （necessity/index/callgraph.py）解释，见其 docstring。

    async def prepare_call_hierarchy(self, filepath: str, line: int,
                                     character: int) -> Optional[list[dict]]:
        """`textDocument/prepareCallHierarchy`。

        ⚠️ 方法名是 `textDocument/prepareCallHierarchy`，**不是**
        `callHierarchy/prepareCallHierarchy` —— 实测后者返回
        `-32601 Unhandled method`（pyright 1.1.414）。只有
        incoming/outgoing 两个请求用 `callHierarchy/` 前缀。

        位置必须指向**符号名**（通常是 selectionRange.start），指向 def
        关键字行会返回空数组且不报错 —— 这是本项目最隐蔽的陷阱。
        """
        server = await self.ensure_server_started(filepath)
        if not server:
            return None
        return await server.send_request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": self._path_to_uri(filepath)},
            "position": {"line": line, "character": character},
        })

    async def incoming_calls(self, item: dict) -> Optional[list[dict]]:
        """`callHierarchy/incomingCalls` —— 谁调用了这个函数。

        `item` 必须是 prepare_call_hierarchy 返回数组中的**原始元素**
        原样回传：pyright 依赖其中的 uri/range/data 字段定位。
        """
        return await self._call_hierarchy_any("callHierarchy/incomingCalls", item)

    async def outgoing_calls(self, item: dict) -> Optional[list[dict]]:
        """`callHierarchy/outgoingCalls` —— 这个函数调用了谁。"""
        return await self._call_hierarchy_any("callHierarchy/outgoingCalls", item)

    async def _call_hierarchy_any(self, method: str, item: dict) -> Optional[list[dict]]:
        """callHierarchy 的 item 自带 uri，无需按文件路由，取任一健康 server。"""
        for server in self._servers.values():
            if server.is_healthy():
                try:
                    return await server.send_request(method, {"item": item})
                except (TimeoutError, RuntimeError) as e:
                    logger.debug(f"LSP '{method}' 失败: {e}")
                    return None
        return None

    async def get_references(self, filepath: str, line: int, character: int,
                             include_declaration: bool = False) -> Optional[list[dict]]:
        """`textDocument/references`。

        `include_declaration` 默认 **False**：传 True 会把定义点本身作为一条
        「引用」返回，建图时会形成自环，对「谁调用了我」毫无信息量。
        （原先硬编码 True —— 调用方无法关闭。）
        """
        server = await self.ensure_server_started(filepath)
        if not server:
            return None
        uri = self._path_to_uri(filepath)
        return await server.send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        })

    async def get_document_symbols(self, filepath: str) -> Optional[list[dict]]:
        """`textDocument/documentSymbol` —— 文件内全部符号（层级结构）。

        ⚠️ 采点规则（实测确认，见 necessity/probe/FINDINGS.md）：
        要查引用/调用关系，位置必须取 `selectionRange.start`（指向**符号名**），
        而不是 `range.start`（指向 def 关键字的缩进）。实测差异：
            range.start          = (45, 0)  -> references 返回 **0** 条
            selectionRange.start = (45, 4)  -> references 返回 **25** 条
        采错点**不报错**，只静默返回空列表 —— 本模块最隐蔽的陷阱。
        """
        server = await self.ensure_server_started(filepath)
        if not server:
            return None
        return await server.send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": self._path_to_uri(filepath)},
        })

    # ── Helpers ─────────────────────────────────────────────────

    def _path_to_uri(self, filepath: str) -> str:
        """Convert filesystem path to file:// URI."""
        abs_path = os.path.abspath(filepath).replace("\\", "/")
        return f"file:///{abs_path}"

    def _handle_config_request(self, params: Any, server_name: str) -> list:
        """Handle workspace/configuration requests from LSP servers."""
        items = params.get("items", []) if isinstance(params, dict) else []
        return [None] * len(items)


# ── Global singleton ────────────────────────────────────────────

_manager: Optional[LSPServerManager] = None
_manager_lock = asyncio.Lock()


async def create_server_manager() -> LSPServerManager:
    """Create and initialize the global LSP manager singleton."""
    global _manager
    async with _manager_lock:
        if _manager is not None:
            return _manager
        _manager = LSPServerManager()
        await _manager.initialize()
        return _manager


async def get_manager() -> Optional[LSPServerManager]:
    """Get the global LSP manager (may be None if not initialized)."""
    return _manager
