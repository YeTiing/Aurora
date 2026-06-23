# file_rw 工具 — 文件读写 + 目录列表 + 路径安全
from __future__ import annotations
import os, json, shutil
from pathlib import Path
from typing import Any
from .base import ToolSpec, safe_resolve_path, truncate_output

FILE_RW_SPEC = ToolSpec(
    name="file_rw",
    description="Read, write, list, or delete files and directories. Supports JSON parsing for structured data. Paths are relative to workspace.",
    parameters={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["read", "write", "list", "delete", "exists", "info", "mkdir", "copy", "move"]},
            "path": {"type": "string", "description": "File or directory path relative to workspace"},
            "content": {"type": "string", "description": "Content to write (for write operation)"},
            "encoding": {"type": "string", "description": "File encoding (default utf-8)"},
            "recursive": {"type": "boolean", "description": "Recursive for list/delete operations"},
            "destination": {"type": "string", "description": "Destination path for copy/move"},
        },
        "required": ["operation", "path"],
    },
    category="filesystem",
)

MAX_READ_SIZE = 1024 * 1024  # 1MB
MAX_LIST_ITEMS = 500
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".next", "target"}

async def file_rw_handler(arguments: dict, workspace: str = ".") -> str:
    op = arguments.get("operation", "read")
    path_str = arguments.get("path", "")
    content = arguments.get("content", "")
    encoding = arguments.get("encoding", "utf-8")
    recursive = arguments.get("recursive", False)
    dest = arguments.get("destination", "")

    try:
        file_path = safe_resolve_path(path_str, workspace)
    except PermissionError as e:
        return f"Error: {e}"

    try:
        if op == "read":
            return _handle_read(file_path, encoding)
        elif op == "write":
            return _handle_write(file_path, content, encoding)
        elif op == "list":
            return _handle_list(file_path, recursive)
        elif op == "delete":
            return _handle_delete(file_path, recursive)
        elif op == "exists":
            return json.dumps({"exists": file_path.exists(), "is_file": file_path.is_file(), "is_dir": file_path.is_dir()})
        elif op == "info":
            return _handle_info(file_path)
        elif op == "mkdir":
            file_path.mkdir(parents=True, exist_ok=True)
            return f"Directory created: {path_str}"
        elif op == "copy":
            dest_path = safe_resolve_path(dest, workspace)
            if file_path.is_file():
                shutil.copy2(file_path, dest_path)
            else:
                shutil.copytree(file_path, dest_path, dirs_exist_ok=True)
            return f"Copied {path_str} -> {dest}"
        elif op == "move":
            dest_path = safe_resolve_path(dest, workspace)
            shutil.move(str(file_path), str(dest_path))
            return f"Moved {path_str} -> {dest}"
        else:
            return f"Unknown operation: {op}"
    except PermissionError as e:
        return f"Permission denied: {e}"
    except FileNotFoundError as e:
        return f"File not found: {e}"
    except Exception as e:
        return f"Error ({type(e).__name__}): {str(e)[:500]}"

def _handle_read(path: Path, encoding: str) -> str:
    if not path.exists():
        return f"File not found: {path.name}"
    if path.stat().st_size > MAX_READ_SIZE:
        stat = path.stat()
        preview = path.read_text(encoding, errors="replace")[:8000]
        return f"File too large ({stat.st_size} bytes). Preview:\n{preview}\n\n[File truncated at 8000 chars]"
    return path.read_text(encoding, errors="replace")

def _handle_write(path: Path, content: str, encoding: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding)
    return f"Wrote {len(content)} bytes to {path.name}"

def _handle_list(path: Path, recursive: bool) -> str:
    if not path.exists():
        return f"Directory not found: {path.name}"
    if not path.is_dir():
        return f"Not a directory: {path.name}"

    lines = []
    count = 0

    def walk(dir_path: Path, prefix: str = "", depth: int = 0):
        nonlocal count
        if depth > 5 and recursive:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            lines.append(f"{prefix}[Permission denied]")
            return

        for entry in entries:
            if count >= MAX_LIST_ITEMS:
                lines.append(f"\n[... {MAX_LIST_ITEMS} item limit reached ...]")
                return
            if entry.name in SKIP_DIRS:
                continue
            if entry.is_dir():
                lines.append(f"{prefix}📁 {entry.name}/")
                count += 1
                if recursive:
                    walk(entry, prefix + "  ", depth + 1)
            else:
                size = entry.stat().st_size
                size_str = f"{size:,}B" if size < 1024 else f"{size/1024:.0f}KB" if size < 1024*1024 else f"{size/1024/1024:.1f}MB"
                lines.append(f"{prefix}📄 {entry.name} ({size_str})")
                count += 1

    walk(path)
    return "\n".join(lines) if lines else "(empty directory)"

def _handle_delete(path: Path, recursive: bool) -> str:
    if not path.exists():
        return f"Path not found: {path.name}"
    if path.is_dir():
        if recursive:
            shutil.rmtree(path)
        else:
            path.rmdir()
    else:
        path.unlink()
    return f"Deleted: {path.name}"

def _handle_info(path: Path) -> str:
    if not path.exists():
        return json.dumps({"exists": False})
    stat = path.stat()
    return json.dumps({
        "name": path.name,
        "path": str(path),
        "is_file": path.is_file(),
        "is_dir": path.is_dir(),
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "created": stat.st_ctime,
        "permissions": oct(stat.st_mode)[-3:],
    }, indent=2, default=str)