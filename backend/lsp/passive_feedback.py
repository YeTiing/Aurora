# -*- coding: utf-8 -*-
"""LSP Passive Feedback — notification handler registration & diagnostic formatting.

Port of cc-haha's src/services/lsp/passiveFeedback.ts.
Registers handlers for textDocument/publishDiagnostics across all servers.
"""

from __future__ import annotations

import logging
from typing import Any

from .diagnostic_registry import get_registry
from .server_manager import LSPServerManager

logger = logging.getLogger("aurora.lsp.feedback")

# LSP DiagnosticSeverity: 1=Error, 2=Warning, 3=Information, 4=Hint
SEVERITY_MAP = {1: "Error", 2: "Warning", 3: "Info", 4: "Hint"}


def map_severity(lsp_severity: int | None) -> str:
    """Map LSP severity number to string."""
    return SEVERITY_MAP.get(lsp_severity or 0, "Error")


def _uri_to_path(uri: str) -> str:
    """Convert file:// URI to filesystem path."""
    if uri.startswith("file://"):
        # file:///C:/... or file:///home/...
        path = uri[7:]  # Remove "file://"
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            # Windows: /C:/... → C:/...
            path = path[1:]
        return path.replace("/", "\\") if "\\" in path or ":" in path else path
    return uri


def format_diagnostics(params: dict) -> list[dict]:
    """Convert LSP PublishDiagnosticsParams to Aurora diagnostic format.

    Returns list of diagnostic file dicts.
    """
    uri = params.get("uri", "")
    filepath = _uri_to_path(uri)
    diagnostics = params.get("diagnostics", [])

    formatted = []
    for diag in diagnostics:
        rng = diag.get("range", {})
        start = rng.get("start", {})
        end = rng.get("end", {})
        formatted.append({
            "filepath": filepath,
            "message": diag.get("message", ""),
            "severity": map_severity(diag.get("severity")),
            "line": start.get("line", 0),
            "character": start.get("character", 0),
            "end_line": end.get("line", 0),
            "end_character": end.get("character", 0),
            "source": diag.get("source", ""),
            "code": str(diag.get("code")) if diag.get("code") is not None else "",
        })

    return formatted


def register_diagnostic_handlers(manager: LSPServerManager) -> dict:
    """Register publishDiagnostics notification handlers on all servers.

    Returns registration result with success/failure counts.
    """
    servers = manager.get_all_servers()
    registry = get_registry()

    result = {
        "total_servers": len(servers),
        "success_count": 0,
        "errors": [],
    }

    for server_name, instance in servers.items():
        # Register the handler on each server instance
        def make_handler(name: str):
            def handler(params: dict) -> None:
                logger.debug(f"LSP diagnostic from '{name}': {len(params.get('diagnostics', []))} items")
                files = format_diagnostics(params)
                if files:
                    # Wrap in expected format
                    uri = _uri_to_path(params.get("uri", ""))
                    registry.register(name, [{"uri": uri, "diagnostics": [
                        {k: v for k, v in d.items() if k not in ("filepath",)}
                        for d in files
                    ]}])
            return handler

        instance.on_notification("textDocument/publishDiagnostics", make_handler(server_name))
        result["success_count"] += 1
        logger.debug(f"Registered diagnostic handler for '{server_name}'")

    logger.info(f"LSP diagnostic handlers registered: {result['success_count']}/{result['total_servers']} servers")
    return result
