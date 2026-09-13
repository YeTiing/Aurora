# -*- coding: utf-8 -*-
"""LSP Configuration — built-in configs for common language servers.

Port of cc-haha's src/services/lsp/config.ts.
Provides sensible defaults for pyright, typescript-language-server, rust-analyzer, gopls.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

from .server_instance import LspServerConfig

# ── Built-in LSP server configs ─────────────────────────────────

BUILTIN_CONFIGS: dict[str, LspServerConfig] = {
    "pyright": LspServerConfig(
        command="pyright-langserver",
        args=["--stdio"],
        extension_to_language={".py": "python", ".pyi": "python"},
        initialization_options={
            "typeCheckingMode": "basic",
            "useLibraryCodeForTypes": True,
        },
    ),
    "pylyzer": LspServerConfig(
        command="pylyzer",
        args=[],
        extension_to_language={".py": "python"},
    ),
    "ruff": LspServerConfig(
        command="ruff",
        args=["server"],
        extension_to_language={".py": "python"},
    ),
    "typescript": LspServerConfig(
        command="typescript-language-server",
        args=["--stdio"],
        extension_to_language={
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
            ".mjs": "javascript",
            ".cjs": "javascript",
        },
    ),
    "rust-analyzer": LspServerConfig(
        command="rust-analyzer",
        args=[],
        extension_to_language={".rs": "rust"},
    ),
    "gopls": LspServerConfig(
        command="gopls",
        args=[],
        extension_to_language={".go": "go"},
    ),
    "lua_ls": LspServerConfig(
        command="lua-language-server",
        args=[],
        extension_to_language={".lua": "lua"},
    ),
    "html": LspServerConfig(
        command="vscode-html-language-server",
        args=["--stdio"],
        extension_to_language={
            ".html": "html",
            ".htm": "html",
        },
    ),
    "css": LspServerConfig(
        command="vscode-css-language-server",
        args=["--stdio"],
        extension_to_language={
            ".css": "css",
            ".scss": "scss",
            ".less": "less",
        },
    ),
    "json": LspServerConfig(
        command="vscode-json-language-server",
        args=["--stdio"],
        extension_to_language={
            ".json": "json",
            ".jsonc": "jsonc",
        },
    ),
    "marksman": LspServerConfig(
        command="marksman",
        args=["server"],
        extension_to_language={
            ".md": "markdown",
            ".mdx": "markdown",
        },
    ),
    "clangd": LspServerConfig(
        command="clangd",
        args=["--background-index"],
        extension_to_language={
            ".c": "c",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".h": "c",
            ".hpp": "cpp",
        },
    ),
}

# ── Extension → server name lookup (built once) ─────────────────

_EXTENSION_MAP: dict[str, list[str]] = {}
_MAP_BUILT = False


def resolve_executable(command: str) -> str:
    """把命令名解析成可直接 spawn 的绝对路径。

    Windows 上 create_subprocess_exec **不套用 PATHEXT**，而 npm 全局安装的
    是 .CMD shim。因此 `which("pyright-langserver")` 说可用、
    `spawn("pyright-langserver")` 却抛 WinError 2 —— 探测与启动的判定不一致，
    失败还会被上层吞成「LSP not initialized」，表现为功能静默不可用。

    这里返回绝对路径；优先 .exe（可直接执行），其次 .cmd/.bat（也能 spawn，
    因为已带扩展名）。找不到则原样返回命令名，让上层按现有逻辑处理缺失。
    """
    if os.name != "nt":
        return shutil.which(command) or command

    found = shutil.which(command)
    if not found:
        return command

    # which 已解析到带扩展名的 shim；直接用它即可（带扩展名就能 spawn）
    return found


def _build_extension_map() -> None:
    global _MAP_BUILT
    if _MAP_BUILT:
        return
    for name, config in BUILTIN_CONFIGS.items():
        for ext in config.extension_to_language:
            normalized = ext.lower()
            _EXTENSION_MAP.setdefault(normalized, []).append(name)
    _MAP_BUILT = True


def get_builtin_configs() -> dict[str, LspServerConfig]:
    """Return all built-in LSP server configurations."""
    return dict(BUILTIN_CONFIGS)


def get_config_for_file(filepath: str) -> Optional[LspServerConfig]:
    """Get the best LSP config for a given file based on its extension."""
    _build_extension_map()
    _, ext = os.path.splitext(filepath)
    ext = ext.lower()
    if ext not in _EXTENSION_MAP:
        return None
    # Use first matching server
    server_name = _EXTENSION_MAP[ext][0]
    return BUILTIN_CONFIGS.get(server_name)


def get_config_by_name(name: str) -> Optional[LspServerConfig]:
    """Get LSP config by server name."""
    return BUILTIN_CONFIGS.get(name)


def find_available_servers() -> dict[str, LspServerConfig]:
    """Return only LSP servers whose command is available on PATH."""
    available = {}
    for name, config in BUILTIN_CONFIGS.items():
        if shutil.which(config.command):
            available[name] = config
    return available


def find_executable(command: str) -> Optional[str]:
    """Find LSP executable on PATH. Returns path or None."""
    return shutil.which(command)
