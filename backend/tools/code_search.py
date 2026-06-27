# code_search 工具 — 代码搜索 + 正则匹配 + 语义搜索
from __future__ import annotations
import re, subprocess, asyncio, fnmatch
from pathlib import Path
from typing import Any
from .base import ToolSpec, safe_resolve_path, truncate_output
import logging
logger = logging.getLogger("aurora")

CODE_SEARCH_SPEC = ToolSpec(
    name="code_search",
    description="Search code in the workspace using regex patterns. Returns file paths, line numbers, and matching line content. Supports file type filtering and context lines.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search pattern (regex supported)"},
            "pattern": {"type": "string", "description": "Alternative to query: literal or regex pattern"},
            "file_pattern": {"type": "string", "description": "Glob pattern for files to search (e.g., '*.py', '*.ts')"},
            "case_sensitive": {"type": "boolean", "description": "Case-sensitive search (default false)"},
            "whole_word": {"type": "boolean", "description": "Match whole words only"},
            "context_lines": {"type": "integer", "description": "Number of context lines before and after match"},
            "max_results": {"type": "integer", "description": "Maximum results to return (default 50)"},
        },
        "required": [],
    },
    category="search",
)

SEARCHABLE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml",
    ".go", ".rs", ".java", ".kt", ".swift", ".c", ".cpp", ".h", ".hpp",
    ".md", ".txt", ".toml", ".cfg", ".ini", ".env", ".sh", ".ps1",
    ".html", ".css", ".scss", ".sql", ".graphql", ".proto",
    ".dockerfile", ".makefile", ".gitignore",
}

async def code_search_handler(arguments: dict, workspace: str = ".") -> str:
    query = arguments.get("query", "") or arguments.get("pattern", "")
    if not query:
        return "Error: No search query or pattern provided."

    file_pattern = arguments.get("file_pattern", "*")
    case_sensitive = arguments.get("case_sensitive", False)
    whole_word = arguments.get("whole_word", False)
    context_lines = arguments.get("context_lines", 0)
    max_results = arguments.get("max_results", 50)

    ws = Path(workspace)
    if not ws.exists():
        return f"Workspace not found: {workspace}"

    # 尝试用 ripgrep 加速
    results = await _ripgrep_search(ws, query, file_pattern, case_sensitive, whole_word, context_lines, max_results)
    if results is not None:
        return results

    # Python 降级搜索
    return _python_search(ws, query, file_pattern, case_sensitive, whole_word, context_lines, max_results)

async def _ripgrep_search(ws: Path, query: str, file_pattern: str, case_sensitive: bool, whole_word: bool, context_lines: int, max_results: int) -> str | None:
    try:
        args = ["rg", "--line-number", "--no-heading", "--color=never"]
        if not case_sensitive:
            args.append("--ignore-case")
        if whole_word:
            args.append("--word-regexp")
        if context_lines > 0:
            args.extend(["-C", str(context_lines)])
        if file_pattern != "*":
            args.extend(["--glob", file_pattern])
        args.extend(["--", query, str(ws)])

        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode("utf-8", errors="replace")

        if not output.strip():
            return f"No matches found for '{query}'"

        lines = output.strip().split("\n")
        if len(lines) > max_results:
            lines = lines[:max_results] + [f"\n[... {len(lines) - max_results} more results truncated ...]"]

        return truncate_output("\n".join(lines), 16000)
    except FileNotFoundError:
        return None  # rg not available
    except asyncio.TimeoutError:
        return None  # fall through
    except Exception:
        return None

def _python_search(ws: Path, query: str, file_pattern: str, case_sensitive: bool, whole_word: bool, context_lines: int, max_results: int) -> str:
    flags = 0 if case_sensitive else re.IGNORECASE
    if whole_word:
        query = rf"\b{re.escape(query)}\b"
    try:
        pattern = re.compile(query, flags)
    except re.error as e:
        return f"Invalid regex pattern: {e}"

    results = []
    try:
        for file_path in ws.rglob(file_pattern):
            if not fnmatch.fnmatch(file_path.name, file_pattern if file_pattern != "*" else "*"):
                continue
            if file_path.suffix not in SEARCHABLE_EXTENSIONS:
                if file_path.is_file():
                    try:
                        rel = file_path.relative_to(ws)
                        if str(rel).startswith(".") or any(p in str(rel) for p in ("node_modules", "__pycache__", ".git", "venv")):
                            continue
                    except Exception: logger.debug('code_search file read failed', exc_info=True)
                    # Check by name match
                    pass
                else:
                    continue

            try:
                content = file_path.read_text("utf-8", errors="replace")
            except Exception:
                continue

            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    if context_lines > 0:
                        start = max(0, i - context_lines - 1)
                        end = min(len(lines), i + context_lines)
                        context_block = []
                        for j in range(start, end):
                            prefix = ">" if j == i - 1 else " "
                            context_block.append(f"{file_path.relative_to(ws)}:{j+1}:{prefix}{lines[j][:200]}")
                        results.append("\n".join(context_block))
                    else:
                        results.append(f"{file_path.relative_to(ws)}:{i}:{line.strip()[:200]}")
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break
    except Exception as e:
        results.append(f"Search error: {e}")

    if not results:
        return f"No matches found for '{query}'"

    return truncate_output("\n".join(results[:max_results]), 16000)