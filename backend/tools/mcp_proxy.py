# MCP 代理 — 外部 MCP Server 连接 + 工具发现 + 调用路由
from __future__ import annotations
import asyncio, json, subprocess, os, signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .base import ToolSpec, ToolCallResult

@dataclass
class MCPServerConfig:
    name: str
    command: str  # e.g., "node" or "python"
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    cwd: str = ""
    auto_restart: bool = True

@dataclass
class MCPServerState:
    config: MCPServerConfig
    process: asyncio.subprocess.Process | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    connected: bool = False
    request_id: int = 0
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    reader_task: asyncio.Task | None = None

class MCPProxy:
    """MCP 协议代理 — 管理外部 MCP Server 生命周期"""

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self):
        self._servers: dict[str, MCPServerState] = {}

    async def connect_server(self, config: MCPServerConfig) -> bool:
        """启动并初始化 MCP Server"""
        if config.name in self._servers:
            await self.disconnect_server(config.name)

        state = MCPServerState(config=config)
        self._servers[config.name] = state

        try:
            state.process = await asyncio.create_subprocess_exec(
                config.command, *config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.cwd or os.getcwd(),
                env={**os.environ, **config.env},
            )

            # 启动 reader
            state.reader_task = asyncio.create_task(self._reader_loop(state))

            # Initialize handshake
            init_result = await self._send_request(state, "initialize", {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "Aurora", "version": "0.1.0"},
            })

            if not init_result or "error" in init_result:
                await self.disconnect_server(config.name)
                return False

            # Send initialized notification
            self._send_notification(state, "notifications/initialized", {})

            # Discover tools
            tools_result = await self._send_request(state, "tools/list", {})
            if tools_result and "tools" in tools_result:
                state.tools = [
                    ToolSpec(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=t.get("inputSchema", {"type": "object", "properties": {}}),
                        exposure="direct",
                        category="mcp",
                    )
                    for t in tools_result.get("tools", [])
                ]

            state.connected = True
            return True
        except Exception as e:
            await self.disconnect_server(config.name)
            return False

    async def disconnect_server(self, name: str):
        state = self._servers.pop(name, None)
        if not state:
            return
        state.connected = False
        if state.reader_task:
            state.reader_task.cancel()
        if state.process:
            try:
                state.process.stdin.close()
                state.process.terminate()
                await asyncio.wait_for(state.process.wait(), timeout=5)
            except Exception:
                try: state.process.kill()
                except: pass

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> ToolCallResult:
        state = self._servers.get(server_name)
        if not state or not state.connected:
            return ToolCallResult(id="", name=tool_name, output="", success=False,
                                 error=f"MCP server '{server_name}' not connected")

        try:
            result = await self._send_request(state, "tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })
            if result and "content" in result:
                text_parts = []
                for c in result["content"]:
                    if c.get("type") == "text":
                        text_parts.append(c.get("text", ""))
                    elif c.get("type") == "resource":
                        text_parts.append(f"[Resource: {c.get('resource', {}).get('uri', '')}]")
                return ToolCallResult(id="", name=tool_name,
                                     output="\n".join(text_parts)[:16384],
                                     success=True)
            elif result and "error" in result:
                return ToolCallResult(id="", name=tool_name, output="", success=False,
                                     error=result["error"].get("message", "Unknown MCP error"))
            return ToolCallResult(id="", name=tool_name, output=str(result)[:16384], success=True)
        except Exception as e:
            return ToolCallResult(id="", name=tool_name, output="", success=False,
                                 error=f"MCP call failed: {str(e)[:500]}")

    def get_tools(self, server_name: str) -> list[ToolSpec]:
        state = self._servers.get(server_name)
        return state.tools if state else []

    def get_all_tools(self) -> list[ToolSpec]:
        tools = []
        for state in self._servers.values():
            tools.extend(state.tools)
        return tools

    def list_servers(self) -> list[dict]:
        return [{"name": s.config.name, "connected": s.connected, "tools_count": len(s.tools)}
                for s in self._servers.values()]

    # ── 内部协议方法 ──
    def _next_id(self, state: MCPServerState) -> int:
        state.request_id += 1
        return state.request_id

    async def _send_request(self, state: MCPServerState, method: str, params: dict, timeout: float = 30) -> dict | None:
        req_id = self._next_id(state)
        request = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        state.pending[req_id] = future

        try:
            state.process.stdin.write((request + "\n").encode())
            await state.process.stdin.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            state.pending.pop(req_id, None)
            return {"error": {"message": f"Request timeout: {method}"}}
        except Exception as e:
            state.pending.pop(req_id, None)
            return {"error": {"message": str(e)}}

    def _send_notification(self, state: MCPServerState, method: str, params: dict):
        try:
            notif = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
            state.process.stdin.write((notif + "\n").encode())
        except Exception:
            pass

    async def _reader_loop(self, state: MCPServerState):
        try:
            while state.process and state.process.stdout:
                line = await state.process.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode("utf-8", errors="replace"))
                    msg_id = data.get("id")
                    if msg_id is not None and msg_id in state.pending:
                        future = state.pending.pop(msg_id)
                        if "error" in data:
                            future.set_result(data)
                        else:
                            future.set_result(data.get("result", {}))
                except json.JSONDecodeError:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            state.connected = False

# 全局实例
mcp_proxy = MCPProxy()