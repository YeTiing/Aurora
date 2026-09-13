# 通用审批门 — 供各工具 handler 在需要时请求审批
# 修复: 之前只有 shell_command 接了审批，file_delete/network/mcp_tool/computer_use/code_exec 均可绕过。
from __future__ import annotations

import logging

logger = logging.getLogger("aurora.approval")


def resolve_approval_policy(manager, arguments: dict):
    """解析本次调用应使用的审批策略。

    优先级：请求级注入（arguments["_approval_policy"]，由 AgentGraph 按会话写入）
    > manager 上的全局策略。必须优先用请求级值：manager 是进程级单例，
    并发会话若各自 set_policy 会互相覆盖，导致严格策略被静默降级。
    """
    from backend.approval import ApprovalPolicy
    raw = ""
    if isinstance(arguments, dict):
        raw = str(arguments.get("_approval_policy", "") or "")
    if raw:
        try:
            return ApprovalPolicy(raw)
        except ValueError:
            logger.warning(f"unknown approval policy {raw!r}, falling back to global")
    return getattr(manager, "policy", None)


def should_request_approval(manager, tool_name: str, arguments: dict, risk) -> bool:
    """判断是否需要审批，兼容只实现了有状态接口的 manager。

    优先走无状态的 policy_needs_approval（并发安全）；若 manager 未实现该方法
    （例如测试替身或外部注入的实现），退回原有的 needs_approval，
    避免因接口变更而抛 AttributeError。

    **无策略上下文时按拒绝处理**（返回 True）。工具被直接调用（不经 AgentGraph，
    因而没有 arguments["_approval_policy"]）且全局策略也未设置时，不能默认放行
    —— 那等于给所有绕过 agent 的调用开了一个无声的后门。此时要求审批会因
    超时被拒，是 fail-closed 的正确表现；需要放行的调用方应显式传入
    arguments["_approval_policy"]="never"。
    """
    checker = getattr(manager, "policy_needs_approval", None)
    if checker is not None:
        policy = resolve_approval_policy(manager, arguments)
        if policy is not None:
            return bool(checker(policy, risk, tool_name))
        # 无请求级策略，且 manager 未提供全局 policy
        logger.warning(
            f"no approval policy available for {tool_name}, requiring approval by default"
        )
        return True
    return bool(manager.needs_approval(risk, tool_name))


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

    失败策略：**fail-closed**。审批系统整体缺失（ImportError）时可容忍放行，
    因为此时全链路都没有审批能力；但审批链路一旦抛异常（SSE 广播失败、
    assess_risk 出错、wait_for_decision 异常），一律按拒绝处理并记录日志。
    安全组件绝不能因自身故障而静默放行。
    """
    try:
        from backend.approval import approval_bridge
        risk = approval_bridge.manager.assess_risk(tool_name, arguments)
        # 策略优先取请求级注入值，避免读全局单例被并发会话互相覆盖
        if not should_request_approval(approval_bridge.manager, tool_name, arguments, risk):
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
    except Exception as e:
        logger.error(
            f"approval gate failed for {tool_name}, denying by default: {type(e).__name__}: {e}",
            exc_info=True,
        )
        return f"denied (approval-error: {type(e).__name__})"
