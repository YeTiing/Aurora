# 工具系统基础 — ToolSpec / ToolExecutor / 安全校验 / 注册表
from __future__ import annotations
import asyncio, json, re, time, traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

# ── 工具定义 ──
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema
    exposure: str = "direct"  # direct | deferred | hidden
    category: str = "general"
    requires_approval: bool = False
    timeout_ms: int = 30000

    def to_openai_function(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict

@dataclass
class ToolCallResult:
    id: str
    name: str
    output: str
    success: bool
    error: str | None = None
    duration_ms: float = 0
    metadata: dict = field(default_factory=dict)

# ── 安全工具 — 路径校验 ──
def safe_resolve_path(target: str, workspace: str) -> Path:
    """防路径穿越：确保目标路径在 workspace 内"""
    ws = Path(workspace).resolve()
    resolved = (ws / target).resolve()
    if not str(resolved).startswith(str(ws)):
        raise PermissionError(f"Path traversal blocked: {target}")
    return resolved

def sanitize_command(command: str) -> str:
    """清洗危险命令"""
    dangerous = ["rm -rf /", "mkfs.", "dd if=", "> /dev/sda", "chmod 777 /"]
    cmd_lower = command.lower()
    for d in dangerous:
        if d in cmd_lower:
            raise PermissionError(f"Dangerous command blocked: '{d}' detected")
    return command

# ── 输出截断 ──
def truncate_output(output: str, max_chars: int = 16384) -> str:
    if len(output) <= max_chars:
        return output
    half = max_chars // 2
    return output[:half] + f"\n\n[... truncated {len(output) - max_chars} chars ...]\n\n" + output[-half:]

# ── 工具处理器协议 ──
class ToolHandler(Protocol):
    async def __call__(self, arguments: dict, workspace: str) -> Any: ...

# ── 工具注册表 ──
class ToolRegistry:
    """统一工具注册、发现、路由"""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._mcp_tools: dict[str, list[ToolSpec]] = {}  # server_name -> [ToolSpec]
        self._call_history: list[ToolCallResult] = []

    def register(self, spec: ToolSpec, handler: ToolHandler):
        self._tools[spec.name] = spec
        self._handlers[spec.name] = handler

    def unregister(self, name: str):
        self._tools.pop(name, None)
        self._handlers.pop(name, None)

    def register_mcp_server(self, server_name: str, tools: list[ToolSpec]):
        self._mcp_tools[server_name] = tools

    def unregister_mcp_server(self, server_name: str):
        self._mcp_tools.pop(server_name, None)

    def list_tools(self, include_mcp: bool = True, category: str | None = None) -> list[ToolSpec]:
        tools = list(self._tools.values())
        if include_mcp:
            for server_tools in self._mcp_tools.values():
                tools.extend(server_tools)
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def list_tools_openai(self, include_mcp: bool = True) -> list[dict]:
        return [t.to_openai_function() for t in self.list_tools(include_mcp)]

    def get_tool(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    async def execute(self, name: str, arguments: dict, workspace: str = ".") -> ToolCallResult:
        """执行工具并返回结果"""
        start = time.time()
        handler = self._handlers.get(name)

        if not handler:
            # 尝试 MCP 工具
            for server_name, tools in self._mcp_tools.items():
                for t in tools:
                    if t.name == name:
                        return await self._execute_mcp(server_name, name, arguments)

            result = ToolCallResult(id="", name=name, output="", success=False,
                                     error=f"Unknown tool: {name}", duration_ms=0)
        else:
            try:
                output = await handler(arguments, workspace)
                duration = (time.time() - start) * 1000
                output_str = str(output)
                truncated = len(output_str) > 65536
                result = ToolCallResult(
                    id="", name=name,
                    output=truncate_output(output_str) if truncated else output_str,
                    success=True, duration_ms=duration,
                    metadata={"truncated": truncated}
                )
            except PermissionError as e:
                result = ToolCallResult(id="", name=name, output="", success=False,
                                         error=f"Permission denied: {e}", duration_ms=(time.time()-start)*1000)
            except asyncio.TimeoutError:
                spec = self._tools.get(name)
                timeout = spec.timeout_ms / 1000 if spec else 30
                result = ToolCallResult(id="", name=name, output="", success=False,
                                         error=f"Timeout after {timeout}s", duration_ms=(time.time()-start)*1000)
            except Exception as e:
                result = ToolCallResult(id="", name=name, output="", success=False,
                                         error=f"{type(e).__name__}: {str(e)[:500]}",
                                         duration_ms=(time.time()-start)*1000)

        self._call_history.append(result)
        if len(self._call_history) > 100:
            self._call_history = self._call_history[-100:]
        return result

    async def _execute_mcp(self, server_name: str, tool_name: str, arguments: dict) -> ToolCallResult:
        return ToolCallResult(id="", name=tool_name, output="", success=False,
                             error=f"MCP server '{server_name}' not connected")

    def stats(self) -> dict:
        recent = self._call_history[-20:]
        success_rate = sum(1 for r in recent if r.success) / max(len(recent), 1)
        return {
            "registered_tools": len(self._tools),
            "mcp_servers": len(self._mcp_tools),
            "total_calls": len(self._call_history),
            "recent_success_rate": f"{success_rate:.0%}",
        }


tool_registry = ToolRegistry()