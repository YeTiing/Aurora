# 通用审批门 — 供各工具 handler 在需要时请求审批
# 修复: 之前只有 shell_command 接了审批，file_delete/network/mcp_tool/computer_use/code_exec 均可绕过。
from __future__ import annotations


async def maybe_request_approval(
    tool_name: str,
    arguments: dict,
    description: str = "",
    session_id: str = "",
    thread_id: str = "",
) -> str | None:
    """按当前审批策略评估并请求审批。

    Returns:
      - None           — 无需审批，直接执行
      - "approved"     — 已批准
      - 其他字符串      — 拒绝/超时原因，调用方应中止执行

    任何导入/配置异常都放行（不因审批系统故障阻塞任务）。
    """
    try:
        from backend.approval import approval_bridge
        risk = approval_bridge.manager.assess_risk(tool_name, arguments)
        if not approval_bridge.manager.needs_approval(risk, tool_name):
            return None
        cmd = description or str(arguments)[:120]
        request = await approval_bridge.request_command_approval(
            session_id=session_id or str(arguments.get("session_id", "")),
            thread_id=thread_id or str(arguments.get("thread_id", arguments.get("session_id", ""))),
            command=cmd,
            risk=risk,
            description=f"{tool_name}: {cmd}",
        )
        decision = await approval_bridge.manager.wait_for_decision(request.id, request.timeout)
        return decision if decision == "approved" else f"denied ({decision})"
    except ImportError:
        return None
    except Exception:
        return None
