# -*- coding: utf-8 -*-
"""LSP Server Instance — single server lifecycle management.

Port of cc-haha's src/services/lsp/LSPServerInstance.ts.
State machine: stopped → starting → running / error; running → stopping → stopped.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .client import LSPClient, create_lsp_client

logger = logging.getLogger("aurora.lsp.instance")

# Transient error codes
LSP_ERROR_CONTENT_MODIFIED = -32801
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5


class LspServerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class LspServerConfig:
    """Configuration for a single LSP server."""
    command: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    cwd: str | None = None
    initialization_options: dict = field(default_factory=dict)
    extension_to_language: dict[str, str] = field(default_factory=dict)
    max_restarts: int = 3
    shutdown_timeout: float = 5.0


class LSPServerInstance:
    """Manages a single LSP server lifecycle."""

    def __init__(self, name: str, config: LspServerConfig):
        self.name = name
        self.config = config
        self._state: LspServerState = LspServerState.STOPPED
        self._start_time: float | None = None
        self._last_error: Exception | None = None
        self._restart_count = 0
        self._crash_recovery_count = 0

        def on_crash(error: Exception) -> None:
            self._state = LspServerState.ERROR
            self._last_error = error
            self._crash_recovery_count += 1

        self._client = create_lsp_client(name, on_crash)

    @property
    def state(self) -> LspServerState:
        return self._state

    @property
    def start_time(self) -> float | None:
        return self._start_time

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def capabilities(self) -> dict:
        return self._client.capabilities

    @property
    def is_initialized(self) -> bool:
        return self._client.is_initialized

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start the LSP server and send initialize."""
        if self._state == LspServerState.RUNNING:
            return

        self._state = LspServerState.STARTING
        self._last_error = None
        logger.info(f"Starting LSP server '{self.name}': {self.config.command} {' '.join(self.config.args)}")

        try:
            await self._client.start(
                self.config.command,
                self.config.args,
                env=self.config.env or None,
                cwd=self.config.cwd,
            )

            # Build initialize params
            init_params = {
                "processId": None,  # Not a child of an editor
                "rootUri": None,
                "capabilities": {
                    "textDocument": {
                        "diagnostic": {"dynamicRegistration": True},
                        "publishDiagnostics": {"relatedInformation": True},
                        "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
                        "definition": {"dynamicRegistration": True},
                        "references": {"dynamicRegistration": True},
                        "completion": {"dynamicRegistration": True},
                    },
                    "workspace": {
                        "configuration": True,
                        "didChangeConfiguration": {"dynamicRegistration": True},
                    },
                },
                "initializationOptions": self.config.initialization_options or {},
                "workspaceFolders": [],
            }

            result = await self._client.initialize(init_params)
            self._state = LspServerState.RUNNING
            self._start_time = time.time()
            logger.info(f"LSP server '{self.name}' started. Caps: {list(result.get('capabilities', {}).keys())}")

        except Exception as e:
            self._state = LspServerState.ERROR
            self._last_error = e
            logger.error(f"LSP server '{self.name}' failed to start: {e}")
            raise

    async def stop(self) -> None:
        """Gracefully stop the LSP server."""
        if self._state in (LspServerState.STOPPED, LspServerState.STOPPING):
            return

        self._state = LspServerState.STOPPING
        logger.info(f"Stopping LSP server '{self.name}'")
        try:
            await self._client.stop()
        except Exception as e:
            logger.debug(f"Error stopping LSP server '{self.name}': {e}")
        self._state = LspServerState.STOPPED

    async def restart(self) -> None:
        """Stop then start."""
        logger.info(f"Restarting LSP server '{self.name}'")
        self._restart_count += 1
        await self.stop()
        await asyncio.sleep(0.5)
        await self.start()

    def is_healthy(self) -> bool:
        """Check if server is running and healthy."""
        return self._state == LspServerState.RUNNING and self._client.is_initialized

    # ── Request / Notification ──────────────────────────────────

    async def send_request(self, method: str, params: Any) -> Any:
        """Send LSP request with retry on transient errors."""
        self._ensure_running()
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return await self._client.send_request(method, params)
            except RuntimeError as e:
                msg = str(e)
                if "content modified" in msg.lower() or f"code {LSP_ERROR_CONTENT_MODIFIED}" in msg:
                    last_error = e
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.debug(f"LSP transient error (attempt {attempt + 1}/{MAX_RETRIES}), retry in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                raise
        raise last_error or RuntimeError("LSP request failed after retries")

    async def send_notification(self, method: str, params: Any) -> None:
        """Send LSP notification (fire-and-forget)."""
        self._ensure_running()
        await self._client.send_notification(method, params)

    def on_notification(self, method: str, handler: Callable) -> None:
        """Register notification handler (can be called before start)."""
        self._client.on_notification(method, handler)

    def on_request(self, method: str, handler: Callable) -> None:
        """Register server→client request handler."""
        self._client.on_request(method, handler)

    # ── Helpers ─────────────────────────────────────────────────

    def _ensure_running(self) -> None:
        if self._state != LspServerState.RUNNING:
            raise RuntimeError(f"LSP server '{self.name}' is not running (state={self._state.value})")
