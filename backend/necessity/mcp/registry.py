"""MCP 工具注册表 + Guard 边界守卫。

⚠️ 本模块的核心设计决策：**不注册 Constraint Guard 工具**。

理由（INTEGRATION.md §1.3 的原文，也是本项目的面试考点，不能「改进」）：

    Constraint Guard 的职责是拦截**所有**工具调用并扫描工作区。MCP 是
    「被宿主调用的服务」—— 服务端只能看见发给自己的请求，协议里没有任何
    机制让它拦截宿主对**其它**工具的调用。做一个 `guard_check` MCP 工具
    只能做到「Agent 主动来问时才检查一次」，既不覆盖所有写入路径，也拦不住
    任何东西，给出的是**虚假的安全感**。
    Guard 必须走宿主集成（adapter/aurora 的 before_tool / scan_workspace）。
    SYSTEM.md §8.2 的原话：「Constraint Guard 做不了 —— MCP 协议无法拦截
    别的工具调用，必须宿主集成。」

因此 `constraint_guard` 不在 tools/list 里；调用它会被 -32601 拒绝，并
**附上这段理由** —— 因为错误消息会进入模型上下文，Agent 应该从错误里学到
「这条路不通，该走宿主集成」，而不是无意义地重试。
"""

from __future__ import annotations

from .base import Tool
from .tools_attribution import ATTRIBUTION_TOOLS
from .tools_context import CONTEXT_TOOLS
from .tools_reduce import REDUCE_TOOLS

__all__ = [
    "build_registry",
    "tool_schemas",
    "guard_boundary_error",
    "GUARD_TOOL_NAMES",
    "GUARD_BOUNDARY_REASON",
]

# 任何指向 Guard 的名字都应给出同一条边界说明
GUARD_TOOL_NAMES = frozenset({
    "constraint_guard", "guard", "guard_check", "guard_intercept", "guard_spec",
})

GUARD_BOUNDARY_REASON = (
    "Constraint Guard 不能通过 MCP 暴露：它必须拦截**所有**工具调用并扫描工作区，"
    "而 MCP 服务端只能看到发给自己的请求，协议无法拦截别的工具调用"
    "（INTEGRATION.md §1.3：『MCP 协议做不到「拦截别的工具调用」』；"
    "GUARD.md §6.2 要求以工作区 diff 扫描为主、工具级预检为辅）。"
    "请改走宿主集成：adapter/aurora 的 before_tool / after_tool / scan_workspace 钩子。"
)


def build_registry() -> dict[str, Tool]:
    """构建工具表。四个工具，**不含 Guard**。"""
    tools = [*REDUCE_TOOLS, *ATTRIBUTION_TOOLS, *CONTEXT_TOOLS]
    return {t.name: t for t in tools}


def tool_schemas() -> list[dict]:
    """tools/list 的返回体。字段名用 MCP 的 camelCase 约定。"""
    out: list[dict] = []
    for tool in build_registry().values():
        entry = {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        if tool.output_schema:
            entry["outputSchema"] = tool.output_schema
        if tool.annotations:
            entry["annotations"] = tool.annotations
        out.append(entry)
    return out


def guard_boundary_error(name: str) -> str:
    """给 tools/call 到 Guard 名字时用的、可进入模型上下文的错误消息。"""
    return f"'{name}' 不是本 MCP 服务提供的工具。{GUARD_BOUNDARY_REASON}"
