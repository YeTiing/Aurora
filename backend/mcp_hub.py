# Aurora MCP 系统 v2 — node_repl + 完整协议 + server 生命周期管理
"""MCP (Model Context Protocol) 集成：连接外部 MCP Server，自动发现工具"""
from __future__ import annotations
import asyncio, json, os, signal, subprocess, sys, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── MCP Server 配置 ──
@dataclass
class MCPServerConfig:
    name: str
    command: str  # e.g., "node", "python", "node_repl.exe"
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    cwd: str = ""
    startup_timeout_sec: int = 120
    auto_restart: bool = False
    description: str = ""

@dataclass
class MCPServerState:
    config: MCPServerConfig
    process: asyncio.subprocess.Process | None = None
    tools: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    connected: bool = False
    request_id: int = 0
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    reader_task: asyncio.Task | None = None
    started_at: float = 0
    capabilities: dict = field(default_factory=dict)

class MCPHub:
    """MCP Server 管理中心 — 启动/停止/发现/调用"""

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self):
        self._servers: dict[str, MCPServerState] = {}
        self._tool_registry = None  # 引用 ToolRegistry

    def bind_tool_registry(self, registry):
        self._tool_registry = registry

    async def start_server(self, config: MCPServerConfig) -> bool:
        if config.name in self._servers:
            await self.stop_server(config.name)

        state = MCPServerState(config=config)
        self._servers[config.name] = state

        try:
            env = {**os.environ, **config.env}
            state.process = await asyncio.create_subprocess_exec(
                config.command, *config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.cwd or os.getcwd(),
                env=env,
            )
            state.started_at = time.time()

            # 启动 reader loop
            state.reader_task = asyncio.create_task(self._reader_loop(state))

            # Initialize
            result = await self._request(state, "initialize", {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}},
                "clientInfo": {"name": "Aurora", "version": "0.2.0"},
            }, timeout=config.startup_timeout_sec)

            if not result or "error" in result:
                await self.stop_server(config.name)
                return False

            state.capabilities = result.get("capabilities", {})

            # Send initialized
            self._notify(state, "notifications/initialized", {})

            # 发现工具
            try:
                tools_result = await self._request(state, "tools/list", {}, timeout=30)
                if tools_result and "tools" in tools_result:
                    state.tools = tools_result.get("tools", [])
            except Exception:
                pass

            # 发现资源
            try:
                resources_result = await self._request(state, "resources/list", {}, timeout=60)
                if resources_result and "resources" in resources_result:
                    state.resources = resources_result.get("resources", [])
            except Exception:
                pass

            state.connected = True

            # 注册到工具注册表
            if self._tool_registry:
                from tools.base import ToolSpec
                for t in state.tools:
                    spec = ToolSpec(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=t.get("inputSchema", {"type": "object", "properties": {}}),
                        exposure="direct",
                        category="mcp",
                    )
                    self._tool_registry.register_mcp_server(config.name, [spec])

            return True
        except Exception:
            await self.stop_server(config.name)
            return False

    async def stop_server(self, name: str):
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
                try:
                    state.process.kill()
                except Exception:
                    pass

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        state = self._servers.get(server_name)
        if not state or not state.connected:
            return {"success": False, "output": "", "error": f"MCP server '{server_name}' not connected"}

        try:
            result = await self._request(state, "tools/call", {
                "name": tool_name,
                "arguments": arguments,
            }, timeout=60)

            if result and "content" in result:
                text_parts = []
                for c in result["content"]:
                    if c.get("type") == "text":
                        text_parts.append(c.get("text", ""))
                    elif c.get("type") == "resource":
                        text_parts.append(f"[Resource: {c.get('resource', {}).get('uri', '')}]")
                return {"success": True, "output": "\n".join(text_parts)[:16384]}
            elif result and "error" in result:
                return {"success": False, "output": "", "error": result["error"].get("message", "Unknown MCP error")}
            return {"success": True, "output": str(result)[:16384]}
        except Exception as e:
            return {"success": False, "output": "", "error": str(e)[:500]}

    def get_tools(self, server_name: str) -> list[dict]:
        state = self._servers.get(server_name)
        return state.tools if state else []

    def list_servers(self) -> list[dict]:
        return [{"name": s.config.name, "connected": s.connected, "tools_count": len(s.tools),
                 "resources_count": len(s.resources), "uptime": int(time.time() - s.started_at) if s.connected else 0}
                for s in self._servers.values()]

    # ── 内部 ──
    def _next_id(self, state: MCPServerState) -> int:
        state.request_id += 1
        return state.request_id

    async def _request(self, state: MCPServerState, method: str, params: dict, timeout: float = 60) -> dict | None:
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
            return {"error": {"message": f"Timeout: {method}"}}
        except Exception as e:
            state.pending.pop(req_id, None)
            return {"error": {"message": str(e)}}

    def _notify(self, state: MCPServerState, method: str, params: dict):
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

mcp_hub = MCPHub()