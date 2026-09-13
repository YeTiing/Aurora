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


def _find_chaining(command: str) -> str | None:
    """检测 shell 链式/替换元字符，返回命中的片段。

    白名单只校验第一个 token，`git status; <任意命令>` 与 `ls && <任意命令>`
    的首 token 仍在白名单内 —— 这正是绕过点。含链式元字符的命令一律拒绝，
    解释器直执行（`python -c` / `node -e`）同样视为代码执行并拒绝。
    """
    import re
    # 链式与命令替换
    m = re.search(r"(&&|\|\||[;|`]|\$\()", command)
    if m:
        return m.group(0)
    # 解释器直执行：等价于任意代码执行，白名单对其无意义
    m = re.search(r"\b(?:python3?|node|npx|deno|bun)\b[^\n]*?(?:\s-c\s|\s-e\s|\s--eval\s)", command)
    if m:
        return m.group(0).strip()
    return None


def _violates_workspace(command: str) -> str | None:
    """workspace-only 模式下的轻量逃逸检查（启发式，返回违规描述或 None）。

    只拦截明显的越界模式：cd 出工作区、写绝对路径、访问系统敏感目录。
    文件类工具已有 safe_resolve_path 强约束，这里是 shell 的兜底。
    """
    import re
    c = command.strip()
    if not c:
        return None
    # cd 逃逸：cd /、cd ~、cd ..、cd C:\
    m = re.search(r'\bcd\s+(/|~|\.\.|\\\\?[A-Za-z]:)', c)
    if m:
        return f"cd escape: '{m.group(0)}'"
    # 写绝对路径（重定向到 /xxx 或 C:\xxx）
    m = re.search(r'(>>?)\s*([/\\]|[A-Za-z]:\\)', c)
    if m:
        return f"write outside workspace: '{m.group(0).strip()}'"
    # 系统敏感路径
    for pat in (r'/etc/', r'/usr/', r'C:\\Windows', r'C:\\Program', r'%APPDATA%', r'%USERPROFILE%', r'~/', r'\$HOME'):
        if re.search(pat, c, re.IGNORECASE):
            return f"sensitive path referenced: {pat}"
    return None


async def shell_handler(arguments: dict, workspace: str = ".") -> dict:
    command = arguments.get("command", "")
    if not command:
        return {"success": False, "stdout": "", "stderr": "No command provided", "exit_code": -1}

    # workspace-only 沙箱边界检查（由 graph 在 workspace-only 模式注入标志）
    if arguments.get("_workspace_boundary"):
        violation = _violates_workspace(command)
        if violation:
            return {"success": False, "stdout": "", "stderr": f"Sandbox (workspace-only): {violation}", "exit_code": -1}

    # 安全校验: 白名单
    if not _is_whitelisted(command):
        return {"success": False, "stdout": "", "stderr": f"Command not whitelisted: {command.split()[0] if command.strip() else command}", "exit_code": -1}

    # 安全校验: 拒绝链式元字符 / 解释器直执行（白名单可被 `git ...; x` 绕过）
    chaining = _find_chaining(command)
    if chaining:
        return {"success": False, "stdout": "", "stderr": f"Command rejected: shell chaining/execution construct '{chaining}' is not allowed", "exit_code": -1}

    # Approval check
    try:
        from backend.approval import approval_bridge
        risk = approval_bridge.manager.assess_risk("shell_command", arguments)
        if approval_bridge.manager.needs_approval(risk, "shell_command"):
            request = await approval_bridge.request_command_approval(
                session_id=str(arguments.get("session_id", "")),
                thread_id=str(arguments.get("thread_id", arguments.get("session_id", ""))),
                command=command,
                risk=risk,
                description=f"Shell: {command[:80]}",
            )
            decision = await approval_bridge.manager.wait_for_decision(request.id, request.timeout)
            if decision != "approved":
                return {"success": False, "stdout": "", "stderr": f"Command approval {decision}", "exit_code": -1}
    except ImportError:
        pass

    # Bash safety classification
    try:
        from backend.bash_classifier import get_classifier
        classifier = get_classifier()
        cls = classifier.classify_pipeline(command)
        if cls.risk.value in ("blocked", "critical"):
            return {"success": False, "stdout": "", "stderr": f"Command blocked: {cls.reason} (risk: {cls.risk.value})", "exit_code": -1}
    except ImportError as e:
        import logging
        logging.getLogger("aurora").warning(f"Bash classifier unavailable: {e}. Falling back to whitelist only.")

    try:
        sanitize_command(command)
    except PermissionError as e:
        return {"success": False, "stdout": "", "stderr": str(e), "exit_code": -1}

    timeout = arguments.get("timeout", 30)

    # 子进程环境：白名单透传，避免把 AURORA_LLM_API_KEY / AURORA_VISION_API_KEY
    # 等凭据注入每一个被执行的子进程（含第三方项目代码）。
    _PASSTHROUGH_ENV = (
        "PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP",
        "USERPROFILE", "SYSTEMROOT", "SystemDrive", "COMSPEC", "PATHEXT",
        "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "PROGRAMDATA", "WINDIR", "HOMEDRIVE", "HOMEPATH", "OS",
        "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "NODE_PATH",
    )
    child_env = {k: v for k, v in os.environ.items() if k in _PASSTHROUGH_ENV}
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["NODE_OPTIONS"] = "--max-old-space-size=512"

    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=child_env,
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
        # 超时必须真正杀死子进程，否则它会继续在后台运行并持续堆积。
        if proc is not None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError, Exception):
                pass
        return {"success": False, "stdout": "", "stderr": f"Command timed out after {timeout}s", "exit_code": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": f"Command not found: {command.split()[0]}", "exit_code": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": f"Error: {str(e)[:500]}", "exit_code": -1}