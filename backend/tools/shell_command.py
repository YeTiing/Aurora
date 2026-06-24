# shell_command 工具 — 沙箱命令执行
from __future__ import annotations
import asyncio, os, subprocess, shlex, tempfile
from pathlib import Path
from typing import Any
from .base import ToolSpec, safe_resolve_path, sanitize_command, truncate_output

SHELL_SPEC = ToolSpec(
    name="shell_command",
    description="Execute a shell command in the workspace directory. Returns stdout and stderr. Use ripgrep (rg) for fast text search. Commands timeout after 30s.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
        },
        "required": ["command"],
    },
    category="execution",
    timeout_ms=30000,
)

COMMAND_WHITELIST = [
    # 基础
    "ls", "dir", "cat", "type", "echo", "pwd", "cd", "mkdir", "rmdir",
    "cp", "copy", "mv", "move", "rm", "del", "touch",
    "find", "grep", "rg", "head", "tail", "wc", "sort", "uniq",
    "diff", "cmp",
    # 开发工具
    "python", "python3", "node", "npm", "npx", "yarn", "pnpm",
    "cargo", "rustc", "go", "gofmt", "java", "javac", "mvn", "gradle",
    "tsc", "ts-node", "eslint", "prettier",
    # Git
    "git",
    # 包管理
    "pip", "pip3", "poetry", "uv",
    # 其他
    "curl", "wget", "tar", "zip", "unzip", "gzip", "gunzip",
    "chmod", "chown", "make", "cmake",
    "dotnet", "docker",
    # Windows
    "dir", "type", "findstr", "where", "tasklist",
]

def _is_whitelisted(command: str) -> bool:
    cmd_parts = command.strip().split()
    if not cmd_parts:
        return False
    base_cmd = cmd_parts[0].lower().replace(".exe", "").replace(".cmd", "")
    # 处理路径形式
    base_cmd = base_cmd.split("\\")[-1].split("/")[-1]
    return base_cmd in COMMAND_WHITELIST

async def shell_handler(arguments: dict, workspace: str = ".") -> dict:
    command = arguments.get("command", "")
    if not command:
        return {"success": False, "stdout": "", "stderr": "No command provided", "exit_code": -1}

    # 安全校验: 白名单
    if not _is_whitelisted(command):
        return {"success": False, "stdout": "", "stderr": f"Command not whitelisted: {command.split()[0] if command.strip() else command}", "exit_code": -1}

    # Bash safety classification
    try:
        from backend.bash_classifier import get_classifier
        classifier = get_classifier()
        cls = classifier.classify_pipeline(command)
        if cls.risk.value in ("blocked", "critical"):
            return {"success": False, "stdout": "", "stderr": f"Command blocked: {cls.reason} (risk: {cls.risk.value})", "exit_code": -1}
    except ImportError:
        pass

    try:
        sanitize_command(command)
    except PermissionError as e:
        return {"success": False, "stdout": "", "stderr": str(e), "exit_code": -1}

    timeout = arguments.get("timeout", 30)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "NODE_OPTIONS": "--max-old-space-size=512"},
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return {
            "success": proc.returncode == 0,
            "stdout": truncate_output(stdout_bytes.decode("utf-8", errors="replace"), 16000),
            "stderr": truncate_output(stderr_bytes.decode("utf-8", errors="replace"), 4000),
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        return {"success": False, "stdout": "", "stderr": f"Command timed out after {timeout}s", "exit_code": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": f"Command not found: {command.split()[0]}", "exit_code": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": f"Error: {str(e)[:500]}", "exit_code": -1}