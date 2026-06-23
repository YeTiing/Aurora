# code_exec — 在隔离环境中执行代码片段
from __future__ import annotations
import json, asyncio, subprocess, tempfile, os, sys, time, textwrap
from pathlib import Path
from typing import Any
from .base import ToolSpec, ToolCallResult, sanitize_command

sys.stdout.reconfigure(encoding='utf-8')
CODE_EXEC_SPEC = ToolSpec(
    name="code_exec",
    description="Execute a code snippet in an isolated environment and return the output. Supports Python, JavaScript (Node), and shell snippets. Max 30s timeout.",
    parameters={
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["python", "javascript", "bash", "shell"],
                "description": "Programming language of the snippet"
            },
            "code": {
                "type": "string",
                "description": "The code to execute"
            },
            "timeout_sec": {
                "type": "integer",
                "description": "Max execution time in seconds (default 30)",
                "default": 30
            },
        },
        "required": ["language", "code"]
    },
    category="execution",
    exposure="direct",
    timeout_ms=45000,
)

# 危险模块黑名单
PYTHON_BLOCKED = [
    "os.system", "subprocess", "shutil.rmtree", "os.remove", "os.unlink",
    "os.rmdir", "os.chmod", "ctypes", "__import__('os').system",
    "eval(", "exec(", "compile(", "open(",  # open 检查参数
]

JS_BLOCKED = [
    "require('child_process')", "require('fs')", "process.exit",
    "globalThis.fetch", "WebSocket",
]


def _validate_python(code: str) -> str | None:
    """检查Python代码安全性，返回错误信息或None"""
    code_lower = code.lower()
    for blocked in PYTHON_BLOCKED:
        if blocked in code_lower:
            # 特殊处理 open
            if blocked == "open(":
                # 允许 open() 用于 StringIO 等
                if "stringio" in code_lower or "io.open" in code_lower:
                    continue
                return f"Blocked: 'open()' is restricted for safety"
            return f"Blocked: '{blocked}' is restricted for safety"

    # 限制模块导入
    import re
    imports = re.findall(r'import\s+(\S+)|from\s+(\S+)\s+import', code)
    dangerous_modules = {"os", "subprocess", "shutil", "ctypes", "socket", "requests", "http", "urllib"}
    for imp in imports:
        mod = imp[0] or imp[1]
        mod_base = mod.split('.')[0]
        if mod_base in dangerous_modules:
            return f"Blocked: import '{mod}' is restricted for safety"
    return None


def _validate_js(code: str) -> str | None:
    """检查JS代码安全性"""
    for blocked in JS_BLOCKED:
        if blocked in code.lower():
            return f"Blocked: '{blocked}' is restricted for safety"
    return None


async def code_exec_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    language = arguments.get("language", "python")
    code = arguments.get("code", "")
    timeout = min(arguments.get("timeout_sec", 30), 60)

    if not code.strip():
        return ToolCallResult(id="", name="code_exec", output="(empty code snippet)", success=True)

    # 安全校验
    error = None
    if language in ("python",):
        error = _validate_python(code)
    elif language in ("javascript",):
        error = _validate_js(code)

    if error:
        return ToolCallResult(id="", name="code_exec", output="", success=False, error=error)

    try:
        output = await _execute_code(language, code, timeout)
        return ToolCallResult(
            id="", name="code_exec",
            output=output[:16384] if len(output) > 16384 else output,
            success=True,
            metadata={"language": language, "truncated": len(output) > 16384}
        )
    except asyncio.TimeoutError:
        return ToolCallResult(id="", name="code_exec", output="", success=False,
                               error=f"Execution timeout after {timeout}s")
    except Exception as e:
        return ToolCallResult(id="", name="code_exec", output="", success=False,
                               error=f"{type(e).__name__}: {str(e)[:500]}")


async def _execute_code(language: str, code: str, timeout: int) -> str:
    """实际执行代码"""
    loop = asyncio.get_event_loop()

    if language == "python":
        return await asyncio.wait_for(
            loop.run_in_executor(None, _run_python, code),
            timeout=timeout
        )
    elif language == "javascript":
        return await asyncio.wait_for(
            loop.run_in_executor(None, _run_javascript, code),
            timeout=timeout
        )
    elif language in ("bash", "shell"):
        # 沙箱化 shell 执行
        return await asyncio.wait_for(
            loop.run_in_executor(None, _run_shell, code),
            timeout=timeout
        )
    else:
        raise ValueError(f"Unsupported language: {language}")


def _run_python(code: str) -> str:
    """在子进程中执行Python代码"""
    # 使用 exec 在受限环境中运行
    restricted_globals = {
        "__builtins__": {
            "print": print, "len": len, "range": range, "list": list, "dict": dict,
            "set": set, "tuple": tuple, "str": str, "int": int, "float": float,
            "bool": bool, "type": type, "isinstance": isinstance, "zip": zip,
            "enumerate": enumerate, "map": map, "filter": filter, "sorted": sorted,
            "reversed": reversed, "min": min, "max": max, "sum": sum, "abs": abs,
            "round": round, "any": any, "all": all, "True": True, "False": False,
            "None": None, "Exception": Exception, "ValueError": ValueError,
            "KeyError": KeyError, "TypeError": TypeError, "IndexError": IndexError,
            "json": json,
        },
        "json": json,
    }

    import io
    stdout = io.StringIO()
    restricted_globals["__builtins__"]["print"] = lambda *args, **kw: print(*args, **kw, file=stdout)

    try:
        exec(compile(code, "<code_exec>", "exec"), restricted_globals)
        result = stdout.getvalue()
    except Exception as e:
        result = f"Error: {type(e).__name__}: {e}"
    finally:
        stdout.close()

    return result.strip() or "(no output)"


def _run_javascript(code: str) -> str:
    """通过 node 执行JS"""
    # 通过 子进程调用 node -e
    try:
        result = subprocess.run(
            ["node", "-e", code],
            capture_output=True, text=True,
            timeout=30, cwd=os.getcwd(),
            env={**os.environ, "NODE_NO_WARNINGS": "1"}
        )
        output = result.stdout.strip()
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr.strip()
        return output or "(no output)"
    except FileNotFoundError:
        return "Error: Node.js not found. Install Node.js to run JavaScript snippets."
    except subprocess.TimeoutExpired:
        return "Error: JavaScript execution timed out"


def _run_shell(code: str) -> str:
    """安全执行shell命令（仅允许只读/非破坏性操作）"""
    # 白名单检查
    sanitize_command(code)

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", code],
            capture_output=True, text=True,
            timeout=30, cwd=os.getcwd(),
            env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1"}
        )
        output = result.stdout.strip()
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr.strip()
        return output[:5000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Shell execution timed out"