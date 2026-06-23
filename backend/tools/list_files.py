# list_files — Codex 同款目录列表工具
from __future__ import annotations
import os, pathlib
from typing import Any
from .base import ToolSpec, ToolCallResult, safe_resolve_path

LIST_FILES_SPEC = ToolSpec(
    name="list_files",
    description="List files and directories in a workspace directory. Use to explore project structure, find files, and understand codebase organization. Supports recursive listing up to a given depth.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to workspace. Use '.' for root."
            },
            "depth": {
                "type": "integer",
                "description": "Recursion depth (1 = root only, default 2, max 5)",
                "default": 2
            },
            "pattern": {
                "type": "string",
                "description": "Optional glob pattern to filter (e.g., '*.py', '*.{ts,tsx}')"
            },
            "show_hidden": {
                "type": "boolean",
                "description": "Show hidden files/folders (starting with .)",
                "default": False
            }
        },
        "required": ["path"]
    },
    category="filesystem",
    exposure="direct",
    timeout_ms=10000,
)

# 黑名单目录
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv",
             ".next", "dist", "build", ".turbo", ".cache", "target", ".idea", ".vscode"}

async def list_files_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    path_str = arguments.get("path", ".")
    depth = min(arguments.get("depth", 2), 5)
    pattern = arguments.get("pattern", "")
    show_hidden = arguments.get("show_hidden", False)

    try:
        target = safe_resolve_path(path_str, workspace) if path_str != "." else pathlib.Path(workspace)
    except PermissionError as e:
        return ToolCallResult(id="", name="list_files", output="", success=False, error=str(e))

    if not target.exists():
        return ToolCallResult(id="", name="list_files", output="",
                               error=f"Directory not found: {path_str}", success=False)
    if target.is_file():
        return ToolCallResult(id="", name="list_files", output=f"{target.name} ({target.stat().st_size} bytes)",
                               success=True, metadata={"is_file": True})

    output_lines = [f"{target.resolve()}"]
    total_files = 0
    total_dirs = 0

    import fnmatch

    def walk(dir_path: pathlib.Path, prefix: str, current_depth: int):
        nonlocal total_files, total_dirs
        if current_depth > depth:
            return

        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            output_lines.append(f"{prefix}[Permission Denied]")
            return

        filtered = []
        for e in entries:
            if not show_hidden and e.name.startswith("."):
                continue
            if e.is_dir() and e.name in SKIP_DIRS:
                continue
            if pattern and e.is_file() and not fnmatch.fnmatch(e.name, pattern):
                continue
            filtered.append(e)

        for i, entry in enumerate(filtered):
            is_last = i == len(filtered) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                output_lines.append(f"{prefix}{connector}📁 {entry.name}/")
                total_dirs += 1
                next_prefix = prefix + ("    " if is_last else "│   ")
                walk(entry, next_prefix, current_depth + 1)
            else:
                size = entry.stat().st_size
                size_str = _format_size(size)
                output_lines.append(f"{prefix}{connector}{entry.name} ({size_str})")
                total_files += 1

    walk(target, "", 1)

    output = "\n".join(output_lines[:200])
    return ToolCallResult(
        id="", name="list_files", output=output[:8192], success=True,
        metadata={
            "path": str(target), "depth": depth,
            "files": total_files, "dirs": total_dirs,
            "truncated": len(output_lines) > 200,
        }
    )

def _format_size(size: int) -> str:
    if size < 1024: return f"{size}B"
    if size < 1048576: return f"{size/1024:.1f}KB"
    return f"{size/1048576:.1f}MB"