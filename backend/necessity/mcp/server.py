"""MCP stdio 服务端 —— 协议状态 + 方法分发（不含真正的 I/O）。

分层的目的（也是可测性的关键）：
    `McpServer.handle_message(msg) -> dict | None` 是**纯函数式**的请求处理器
    —— 直接可调用，不碰 stdin/stdout。测试因此可以覆盖整个协议面（initialize
    / tools/list / tools/call / 错误码 / Guard 边界）而不起子进程、不装 MCP
    client。transport.py 的 `run_stdio` 只是把处理器接到真实流上的一层壳。

关于「工具抛异常不能让服务端崩溃」（硬要求）：
    宿主会话与 MCP 进程同生共死 —— 一个未捕获异常退出进程，宿主整场会话就
    断了。所以 `tools/call` 的**任何**异常都被转成 `isError:true` 的工具结果
    （不是 JSON-RPC error），只有协议层错误（未知方法 / 参数坏到无法识别工具）
    才用 JSON-RPC error 码。

MCP 方法集（本层实现的是 2024-11-05 起的稳定子集）：
    initialize / notifications/initialized / ping / shutdown / exit
    tools/list / tools/call
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .jsonrpc import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    JsonRpcError,
    error_response,
    success_response,
)
from .base import ToolError, ToolOutcome
from .registry import build_registry, guard_boundary_error, GUARD_TOOL_NAMES, tool_schemas

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

__all__ = ["McpServer"]

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "necessity"
SERVER_VERSION = "0.1.0"


class McpServer:
    """协议状态机 + 请求分发。`handle_message` 可直接调用（测试入口）。"""

    def __init__(self, tools: dict | None = None, *, server_name: str = SERVER_NAME,
                 version: str = SERVER_VERSION):
        self.tools = tools if tools is not None else build_registry()
        self.server_name = server_name
        self.version = version
        self.initialized = False
        self.client_info: dict = {}
        self.protocol_version: str = ""
        self.should_exit = False
        self.stderr = sys.stderr
        self._log("necessity MCP server 就绪，每行一条日志（stdout 仅供协议使用）")

    # ── 日志 / 内部 ──────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        """日志一律走 stderr —— stdout 是协议通道，混入文本会破坏分帧。"""
        try:
            self.stderr.write(f"[necessity-mcp] {msg}\n")
            self.stderr.flush()
        except Exception:
            pass

    def _capabilities(self) -> dict:
        # tools.listChanged=false：工具集在单次会话内固定。
        # 刻意不声明 resources / prompts —— 本服务不实现它们，声明了就是撒谎。
        return {"tools": {"listChanged": False}}

    # ── 请求处理器（可直接调用）──────────────────────────────────

    def handle_message(self, msg: dict) -> dict | None:
        """处理一条 JSON-RPC 消息。返回值 None 表示「通知，无需响应」。

        约定：凡是以 `notifications/` 开头，或（JSON-RPC 2.0）**缺少 id** 的
        消息都按通知处理 —— 后者是协议规定：没有 id 就不能回应。
        """
        if not isinstance(msg, dict):
            return error_response(None, INVALID_REQUEST, "请求必须是 JSON 对象")

        method = msg.get("method")
        mid = msg.get("id", None)
        params = msg.get("params")
        is_notification = (
            method is None
            or str(method).startswith("notifications/")
            or "id" not in msg
            or mid is None
        )
        if method is None or not isinstance(method, str):
            if is_notification:
                return None
            return error_response(mid, INVALID_REQUEST, "缺少合法的 'method' 字段")
        if params is not None and not isinstance(params, dict):
            if is_notification:
                return None
            return error_response(mid, INVALID_PARAMS, "'params' 必须是对象")
        try:
            result = self._dispatch(method, params or {})
        except JsonRpcError as e:
            if is_notification:
                self._log(f"通知 {method} 处理失败: {e.message}")
                return None
            return error_response(mid, e.code, e.message)
        except Exception as e:   # 协议层兜底：绝不因未预期异常断连接
            self._log(f"{method} 未预期异常: {type(e).__name__}: {e}")
            if is_notification:
                return None
            return error_response(mid, -32603,
                                  f"内部错误: {type(e).__name__}: {e}")
        if is_notification:
            return None
        return success_response(mid, result)

    # ── 方法分发 ────────────────────────────────────────────────

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method in ("notifications/initialized", "initialized"):
            self.initialized = True
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": tool_schemas()}
        if method == "tools/call":
            return self._tools_call(params)
        if method == "shutdown":
            return {}
        if method == "exit":
            self.should_exit = True
            return {}
        raise JsonRpcError(METHOD_NOT_FOUND, f"未知方法: {method!r}")
        # 说明：不存在 tools/call 之外的「Guard 方法」；Guard 是通过
        # constraint_guard 这个工具名被调用时才拒绝的，见 _tools_call。

    def _initialize(self, params: dict) -> dict:
        # 重复 initialize 是协议错误；但这里**不报错**，只按已完成应答并记日志。
        # 理由：不同宿主的客户端行为不一（重连、代理重放），对它报错会直接
        # 断掉一场本来能跑完的会话 —— 收益远小于代价。
        if self.initialized:
            self._log("收到重复 initialize，按已完成握手应答")
        client = params.get("clientInfo")
        self.client_info = client if isinstance(client, dict) else {}
        self.protocol_version = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        self.initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": self._capabilities(),
            "serverInfo": {"name": self.server_name, "version": self.version},
        }

    def _tools_call(self, params: dict) -> dict:
        name = params.get("name")
        if not name or not isinstance(name, str):
            raise JsonRpcError(INVALID_PARAMS, "'tools/call' 需要字符串参数 'name'")

        # ── Guard 边界：不是「未知工具」，而是「本协议做不到」 ──
        if name in GUARD_TOOL_NAMES:
            raise JsonRpcError(METHOD_NOT_FOUND, guard_boundary_error(name))

        tool = self.tools.get(name)
        if tool is None:
            raise JsonRpcError(
                METHOD_NOT_FOUND,
                f"未知工具 {name!r}。可用工具: {', '.join(sorted(self.tools))}",
            )

        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(INVALID_PARAMS, "工具参数 'arguments' 必须是对象")

        return self._run_tool(tool, arguments)

    def _run_tool(self, tool, arguments: dict) -> dict:
        """执行工具。**任何异常都变成 isError 结果，绝不上抛。**"""
        try:
            outcome = tool.fn(arguments)
        except ToolError as e:
            return self._tool_error(tool.name, e.message)
        except JsonRpcError as e:
            return self._tool_error(tool.name, f"{e.message}", code=e.code)
        except Exception as e:
            # 工具内部崩溃（含 core 抛出的任何异常）：给 Agent 可读的错误，
            # 并明确「服务端仍存活」—— 这比堆栈更有用。
            self._log(f"工具 {tool.name} 抛异常: {type(e).__name__}: {e}")
            return self._tool_error(
                tool.name, f"{type(e).__name__}: {e}")
        return self._tool_ok(outcome)

    @staticmethod
    def _tool_ok(outcome: ToolOutcome) -> dict:
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": outcome.text}],
            "isError": False,
        }
        if outcome.structured is not None:
            # 两个都给：text 给人/模型看，structuredContent 给程序解析。
            # text 里再塞一份 JSON 是为了兼容只认 content 的老宿主。
            result["structuredContent"] = outcome.structured
            try:
                result["content"].append({
                    "type": "text",
                    "text": json.dumps(outcome.structured, ensure_ascii=False,
                                       indent=2, default=str),
                })
            except (TypeError, ValueError):
                pass
        return result

    @staticmethod
    def _tool_error(tool_name: str, message: str, code: int = INVALID_PARAMS) -> dict:
        text = (f"工具 {tool_name} 执行失败（code={code}）：{message}\n"
                "（MCP 服务端仍在运行，可以继续调用其它工具。）")
        return {
            "content": [{"type": "text", "text": text}],
            "isError": True,
            "structuredContent": {"error": message, "code": code, "tool": tool_name},
        }
