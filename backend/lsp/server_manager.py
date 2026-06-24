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

    async def initialize(self, server_names: list[str] | None = None) -> None:
        """Initialize all available LSP servers (or specific ones)."""
        if self._initialization_state == "pending":
            return
        if self._initialization_state == "success":
            return  # Already initialized

        self._initialization_state = "pending"

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
            await instance.start()
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

    async def get_references(self, filepath: str, line: int, character: int) -> Optional[list[dict]]:
        """Get references at a position."""
        server = await self.ensure_server_started(filepath)
        if not server:
            return None
        uri = self._path_to_uri(filepath)
        return await server.send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True},
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
