# -*- coding: utf-8 -*-
"""Aurora LSP Integration."""
from __future__ import annotations
from .client import LSPClient, create_lsp_client
from .config import LspServerConfig as LSPConfig, get_builtin_configs, get_config_for_file, find_available_servers
from .diagnostic_registry import DiagnosticRegistry, get_registry
from .passive_feedback import register_diagnostic_handlers, format_diagnostics
from .server_instance import LSPServerInstance, LspServerState
from .server_manager import LSPServerManager, create_server_manager, get_manager

__all__ = [
    "LSPClient", "create_lsp_client",
    "LSPConfig", "get_builtin_configs", "get_config_for_file", "find_available_servers",
    "DiagnosticRegistry", "get_registry",
    "register_diagnostic_handlers", "format_diagnostics",
    "LSPServerInstance", "LspServerState",
    "LSPServerManager", "create_server_manager", "get_manager",
]