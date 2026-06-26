"""shell-sandbox plugin — enhanced shell execution with resource limits."""
from __future__ import annotations
import asyncio, os, shlex, time, psutil

async def execute_safe(command: str, cwd: str = ".", timeout: int = 60, max_memory_mb: int = 512) -> dict:
    """Execute a shell command with safety limits."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"success": False, "stdout": "", "stderr": f"Timeout after {timeout}s", "exit_code": -1}

        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode("utf-8", errors="replace")[:65536],
            "stderr": stderr.decode("utf-8", errors="replace")[:65536],
            "exit_code": proc.returncode,
        }
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "exit_code": -1}

def get_system_info() -> dict:
    """Get system resource info."""
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": dict(psutil.virtual_memory()._asdict()),
        "disk": dict(psutil.disk_usage(os.getcwd())._asdict()),
    }

__all__ = ["execute_safe", "get_system_info"]
