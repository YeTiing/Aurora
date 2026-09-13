# 连接器工具 — connector_call：把 8 个 OAuth 连接器暴露给 Agent
# 修复: 之前连接器只有 OAuth 链接 API，Agent 工具环完全用不上。
# 现在通过白名单 action 映射 + 审批门 + 连接状态检查形成闭环。
from __future__ import annotations
import asyncio, inspect, json, logging
from typing import Any

from .base import ToolSpec, ToolCallResult

logger = logging.getLogger("aurora")

# 每个连接器允许的 action -> 实例方法名（白名单，杜绝任意方法调用）
_CONNECTOR_ACTIONS: dict[str, dict[str, str]] = {
    "github": {
        "get_user": "get_user", "list_repos": "list_repos", "search_code": "search_code",
        "get_file": "get_file", "list_issues": "list_issues", "test_connection": "test_connection",
    },
    "gmail": {
        "get_profile": "get_profile", "list_messages": "list_messages",
        "send_message": "send_message", "test_connection": "test_connection",
    },
    "google_drive": {
        "list_files": "list_files", "get_file": "get_file",
        "create_file": "create_file", "test_connection": "test_connection",
    },
    "linear": {
        "list_issues": "list_issues", "get_issue": "get_issue",
        "create_issue": "create_issue", "test_connection": "test_connection",
    },
    "notion": {
        "search": "search", "get_page": "get_page",
        "create_page": "create_page", "test_connection": "test_connection",
    },
    "slack": {
        "list_channels": "list_channels", "post_message": "post_message",
        "list_users": "list_users", "test_connection": "test_connection",
    },
    "figma": {
        "get_file": "get_file", "get_file_nodes": "get_file_nodes",
        "get_comments": "get_comments", "test_connection": "test_connection",
    },
    "google_calendar": {
        "test_connection": "test_connection",
    },
}

# 写类 action（发送/创建/发布）→ 高风险，on-request 下需审批
_WRITE_ACTIONS = {
    "send_message", "create_file", "create_issue", "create_page", "post_message",
}

_CONNECTOR_DESC = "connector_call lets you use connected external accounts as tools. " \
    "First check the account is connected (connector_call test_connection); if not, tell the user " \
    "to link it via the connectors settings. " \
    "Connectors: " + ", ".join(f"{k}({'/'.join(v)})" for k, v in _CONNECTOR_ACTIONS.items())

CONNECTOR_CALL_SPEC = ToolSpec(
    name="connector_call",
    description=_CONNECTOR_DESC,
    parameters={
        "type": "object",
        "properties": {
            "connector": {
                "type": "string", "enum": list(_CONNECTOR_ACTIONS.keys()),
                "description": "Which connected account to use",
            },
            "action": {
                "type": "string",
                "description": "Which operation to perform (see connector-specific actions)",
            },
            "params": {
                "type": "object",
                "description": "Action arguments (e.g. owner/repo/path for github get_file; to/subject/body for gmail send_message)",
            },
        },
        "required": ["connector", "action"],
    },
    category="connectors",
    timeout_ms=60000,
)


async def connector_call_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    connector = str(arguments.get("connector", "")).lower()
    action = str(arguments.get("action", ""))
    params = arguments.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    actions = _CONNECTOR_ACTIONS.get(connector)
    if not actions:
        return ToolCallResult(id="", name="connector_call", output="", success=False,
                              error=f"Unknown connector '{connector}'. Available: {', '.join(_CONNECTOR_ACTIONS)}")
    fn_name = actions.get(action)
    if not fn_name:
        return ToolCallResult(id="", name="connector_call", output="", success=False,
                              error=f"Unknown action '{action}' for {connector}. Available: {', '.join(actions)}")

    from backend.connectors.base import get_registry
    registry = get_registry()
    inst = registry.get(connector)
    if inst is None:
        return ToolCallResult(id="", name="connector_call", output="", success=False,
                              error=f"Connector '{connector}' not registered")
    if not inst.is_connected():
        return ToolCallResult(id="", name="connector_call", output="", success=False,
                              error=f"Connector '{connector}' is not connected. Link it in connector settings first.")

    # 写操作审批门
    if action in _WRITE_ACTIONS:
        try:
            from .approval_gate import maybe_request_approval
            decision = await maybe_request_approval(
                "connector_call", arguments, description=f"connector {connector}.{action}({str(params)[:100]})",
            )
            if decision is not None and decision != "approved":
                return ToolCallResult(id="", name="connector_call", output="", success=False,
                                      error=f"Action {connector}.{action} {decision}")
        except Exception:
            pass

    fn = getattr(inst, fn_name, None)
    if fn is None or not callable(fn):
        return ToolCallResult(id="", name="connector_call", output="", success=False,
                              error=f"Method '{fn_name}' not available on {connector}")

    try:
        # 只传签名支持的参数
        sig = inspect.signature(fn)
        kwargs = {k: v for k, v in params.items() if k in sig.parameters}
        if asyncio.iscoroutinefunction(fn):
            result = await fn(**kwargs)
        else:
            result = fn(**kwargs)
        text = json.dumps(result, ensure_ascii=False, default=str) if not isinstance(result, str) else result
        return ToolCallResult(id="", name="connector_call", output=text[:16384], success=True,
                              metadata={"connector": connector, "action": action})
    except Exception as e:
        logger.error(f"connector_call {connector}.{action} failed", exc_info=True)
        return ToolCallResult(id="", name="connector_call", output="", success=False,
                              error=f"{type(e).__name__}: {str(e)[:500]}")
