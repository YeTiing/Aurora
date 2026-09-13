"""Necessity MCP Server —— 能力的进程外出口（stdio / JSON-RPC 2.0，手写）。

INTEGRATION.md §1.3 的边界（本层恪守，不「改进」）：

    Diff Reducer        ✅ 可以   —— 离线、只读、输入是 diff + 仓库路径
    Failure Attribution ✅ 可以   —— 离线分析
    Context Paging      ⚠️ 部分   —— 需宿主不提供原生 read_file，依赖 Agent 配合
    Constraint Guard    ❌ 不能   —— 必须拦截**所有**工具调用 + 扫描工作区，
                                   MCP 无法拦截别的工具调用（GUARD.md §6.2）

所以本包只注册前三个能力的工具；`constraint_guard` 不在 tools/list 里，
调用它会被 -32601 拒绝并附上边界理由（见 registry.GUARD_BOUNDARY_REASON）。

零依赖：分帧与 JSON-RPC 全部手写（mcp/jsonrpc.py），不引入 `mcp` / `fastmcp`。
"""

from .jsonrpc import FrameReader, encode_line, encode_message, error_response, success_response
from .registry import build_registry, guard_boundary_error, tool_schemas
from .server import McpServer
from .transport import main, run_stdio

__all__ = [
    "FrameReader",
    "McpServer",
    "build_registry",
    "encode_line",
    "encode_message",
    "error_response",
    "guard_boundary_error",
    "main",
    "run_stdio",
    "success_response",
    "tool_schemas",
]
