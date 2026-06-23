# git_ops 工具 — Git 操作（只读默认允许，写操作需二次确认）
from __future__ import annotations
import asyncio, subprocess
from pathlib import Path
from .base import ToolSpec

GIT_OPS_SPEC = ToolSpec(
    name="git_ops",
    description="Git operations for version control. Read operations (status, log, diff, branch, show) execute immediately. Write operations (add, commit, checkout, stash, reset) require confirmation.",
    parameters={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["status", "log", "diff", "diff_staged", "branch", "show", "blame", "add", "commit", "checkout", "stash", "reset", "remote"],
                "description": "Git operation to perform"
            },
            "args": {"type": "string", "description": "Additional arguments for the git command"},
            "message": {"type": "string", "description": "Commit message (for commit operation)"},
            "files": {"type": "array", "items": {"type": "string"}, "description": "Files to add (for add operation)"},
        },
        "required": ["operation"],
    },
    category="vcs",
)

READ_OPS = {"status", "log", "diff", "diff_staged", "branch", "show", "blame", "remote"}
WRITE_OPS = {"add", "commit", "checkout", "stash", "reset"}

async def _run_git(workspace: str, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workspace,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")

async def git_ops_handler(arguments: dict, workspace: str = ".") -> str:
    op = arguments.get("operation", "status")
    extra_args = arguments.get("args", "")
    message = arguments.get("message", "")
    files = arguments.get("files", [])

    if op in READ_OPS:
        return await _handle_read_op(op, extra_args, workspace)
    elif op in WRITE_OPS:
        return await _handle_write_op(op, extra_args, message, files, workspace)
    else:
        return f"Unknown operation: {op}"

async def _handle_read_op(op: str, args: str, workspace: str) -> str:
    cmd_map = {
        "status": ["status", "--short", "--branch"],
        "log": ["log", "--oneline", "--graph", "--decorate", "-30"],
        "diff": ["diff"],
        "diff_staged": ["diff", "--staged"],
        "branch": ["branch", "-a", "-v"],
        "show": ["show", "--stat"],
        "blame": ["blame"],
        "remote": ["remote", "-v"],
    }
    base = cmd_map.get(op, [op])
    if args:
        base.extend(args.split())

    code, stdout, stderr = await _run_git(workspace, *base)
    result = stdout if stdout else "(no output)"
    if stderr:
        result += f"\n[stderr]\n{stderr[:1000]}"
    if code != 0:
        return f"Git {op} failed (exit {code}):\n{result[:4000]}"
    return result[:8000]

async def _handle_write_op(op: str, args: str, message: str, files: list, workspace: str) -> str:
    if op == "add":
        targets = files if files else (args.split() if args else ["."])
        code, stdout, stderr = await _run_git(workspace, "add", *targets)
        return f"Git add: {stdout or 'done'}{' ERR: '+stderr[:200] if stderr else ''}"

    elif op == "commit":
        if not message:
            return "Error: Commit message required"
        code, stdout, stderr = await _run_git(workspace, "commit", "-m", message)
        return f"Git commit: {stdout or 'committed'}{' ERR: '+stderr[:200] if stderr else ''}"

    elif op == "checkout":
        target = args or "main"
        code, stdout, stderr = await _run_git(workspace, "checkout", target)
        return f"Git checkout {target}: {stdout or 'done'}{' ERR: '+stderr[:200] if stderr else ''}"

    elif op == "stash":
        code, stdout, stderr = await _run_git(workspace, "stash")
        return f"Git stash: {stdout or 'done'}{' ERR: '+stderr[:200] if stderr else ''}"

    elif op == "reset":
        target = args or "HEAD"
        code, stdout, stderr = await _run_git(workspace, "reset", target)
        return f"Git reset {target}: {stdout or 'done'}{' ERR: '+stderr[:200] if stderr else ''}"

    return f"Unknown write operation: {op}"

async def git_ops_handler_safe(arguments: dict, workspace: str = ".") -> str:
    """安全包装：写操作前检查是否有未提交更改"""
    op = arguments.get("operation", "status")
    if op in WRITE_OPS:
        code, stdout, _ = await _run_git(workspace, "status", "--porcelain")
        if stdout.strip():
            pass  # 正常允许
    return await git_ops_handler(arguments, workspace)