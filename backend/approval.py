# Approval 审批系统 — exec_approval / patch_approval / request_user_input
from __future__ import annotations
import time, uuid, asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Awaitable

from backend.agent.sse_events import SSEEvent, SSEEventType, sse_bus

class ApprovalPolicy(Enum):
    NEVER = "never"           # 自动执行，无需审批
    ON_FAILURE = "on-failure" # 仅在失败时提示
    ON_REQUEST = "on-request" # 高风险操作请求确认
    UNTRUSTED = "untrusted"   # 严格模式，所有操作需确认


# 默认策略。原为 NEVER，导致开箱状态下 Agent 可无确认执行 shell/写文件/删文件。
# 现为 ON_REQUEST：只对 HIGH/CRITICAL 风险（shell、写文件、删文件、网络写）询问。
DEFAULT_APPROVAL_POLICY = ApprovalPolicy.ON_REQUEST

class RiskLevel(Enum):
    LOW = "low"         # 读操作、代码搜索
    MEDIUM = "medium"   # 文件写入、补丁应用
    HIGH = "high"       # 外部网络请求、git push
    CRITICAL = "critical"  # 系统命令、安装包

@dataclass
class ApprovalRequest:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: str = ""       # exec_command / apply_patch / web_fetch / shell
    command: str = ""
    file_path: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    description: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending / approved / denied
    timeout: float = 30.0

class ApprovalManager:
    """审批管理器 — 对齐 Codex require_escalated 模式"""

    def __init__(self, policy: ApprovalPolicy = DEFAULT_APPROVAL_POLICY):
        self.policy = policy
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []
        self._callbacks: dict[str, Callable] = {}

    def set_policy(self, policy: ApprovalPolicy):
        self.policy = policy

    def needs_approval(self, risk: RiskLevel, tool_name: str = "") -> bool:
        return self.policy_needs_approval(self.policy, risk, tool_name)

    @staticmethod
    def policy_needs_approval(policy: ApprovalPolicy, risk: RiskLevel, tool_name: str = "") -> bool:
        """按给定策略判断是否需要审批（无状态，可安全并发）。

        独立成静态方法的原因：approval_manager 是进程级单例，而
        AgentGraph._apply_approval_mode 会按【请求】把 approval_mode 写到
        该单例上。并发会话下后到的请求会覆盖先到的策略 —— 会话 A 用
        'never'、会话 B 用 'untrusted' 时，A 可能被静默降级为放行。
        调用方应携带自己的 policy 走本方法，而不是读全局 self.policy。
        """
        if policy == ApprovalPolicy.NEVER:
            return False
        if policy == ApprovalPolicy.ON_FAILURE:
            return False  # 只在失败时触发
        if policy == ApprovalPolicy.UNTRUSTED:
            return True
        if policy == ApprovalPolicy.ON_REQUEST:
            return risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        return False

    def assess_risk(self, tool_name: str, arguments: dict) -> RiskLevel:
        if tool_name in ("code_search", "list_files", "view_image", "todo_write", "plan_update",
                         "session_export_tool", "verify_plan", "web_search"):
            return RiskLevel.LOW
        if tool_name in ("file_rw", "apply_patch", "git_ops"):
            op = str(arguments.get("operation", "")) if isinstance(arguments, dict) else str(arguments)
            # 删除/覆盖是高风险写操作
            if "delete" in op or "rm" in op:
                return RiskLevel.CRITICAL
            if "write" in op or "patch" in op or "commit" in op or "push" in op:
                return RiskLevel.MEDIUM
            return RiskLevel.LOW
        if tool_name in ("web_fetch", "send_message"):
            # POST 是网络写操作，提级；GET 读取保持 MEDIUM
            if isinstance(arguments, dict) and str(arguments.get("method", "GET")).upper() == "POST":
                return RiskLevel.HIGH
            return RiskLevel.MEDIUM
        if tool_name in ("shell_command", "code_exec", "computer_use"):
            cmd = str(arguments).lower()
            if any(d in cmd for d in ("rm -rf", "format", "del /", "rd /", "drop", "shutdown", "format ")):
                return RiskLevel.CRITICAL
            return RiskLevel.HIGH
        if tool_name in ("browser_use", "browser_relay", "mcp_proxy"):
            # 浏览器导航/表单提交、MCP 工具调用（可能执行任意操作）
            if isinstance(arguments, dict):
                method = str(arguments.get("method", ""))
                if tool_name == "browser_use" and method in ("click", "type", "submit", "evaluate", "press_key"):
                    return RiskLevel.HIGH
                if tool_name == "mcp_proxy":
                    server = str(arguments.get("server", ""))
                    tname = str(arguments.get("tool", ""))
                    if any(k in (server + "/" + tname).lower() for k in ("shell", "exec", "delete", "write", "install", "push", "rm")):
                        return RiskLevel.CRITICAL
                    return RiskLevel.MEDIUM
            return RiskLevel.MEDIUM
        if tool_name in ("plugin_install", "install_plugin", "package_manager"):
            return RiskLevel.CRITICAL
        if tool_name in ("cron_tool", "skin_tool", "memory", "re_tool", "detective_tool", "lsp_tool"):
            return RiskLevel.LOW
        if tool_name == "connector_call":
            # 写类 action（发送/创建/发布）高风险
            action = str(arguments.get("action", "")) if isinstance(arguments, dict) else ""
            if action in ("send_message", "create_file", "create_issue", "create_page", "post_message"):
                return RiskLevel.HIGH
            return RiskLevel.LOW
        return RiskLevel.MEDIUM

    def create_request(self, tool_name: str, cmd: str = "", file_path: str = "",
                       risk: RiskLevel = RiskLevel.LOW, description: str = "") -> ApprovalRequest:
        req = ApprovalRequest(
            type=tool_name, command=cmd, file_path=file_path,
            risk_level=risk, description=description or f"Execute {tool_name}",
        )
        self._pending[req.id] = req
        return req

    def approve(self, request_id: str) -> bool:
        req = self._pending.pop(request_id, None)
        if req:
            req.status = "approved"
            self._history.append(req)
            # Wake up any waiters
            key = f"_evt_{request_id}"
            evt = getattr(self, key, None)
            if evt:
                evt.set()
            return True
        return False

    def deny(self, request_id: str) -> bool:
        req = self._pending.pop(request_id, None)
        if req:
            req.status = "denied"
            self._history.append(req)
            key = f"_evt_{request_id}"
            evt = getattr(self, key, None)
            if evt:
                evt.set()
            return True
        return False

    def get_pending(self) -> list[ApprovalRequest]:
        return [r for r in self._pending.values() if r.status == "pending"]

    async def wait_for_decision(self, request_id: str, timeout: float | None = None) -> str:
        timeout = timeout or 30.0
        event = asyncio.Event()
        key = f"_evt_{request_id}"
        setattr(self, key, event)
        try:
            # Check if already decided
            request = self._pending.get(request_id)
            if request is None:
                for item in reversed(self._history):
                    if item.id == request_id:
                        return item.status
                return "missing"
            if request.status != "pending":
                return request.status
            # Wait for signal or timeout
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return "timeout"
            # Re-check status after event
            request = self._pending.get(request_id)
            if request and request.status != "pending":
                return request.status
            for item in reversed(self._history):
                if item.id == request_id:
                    return item.status
            return "timeout"
        finally:
            try:
                delattr(self, key)
            except AttributeError:
                pass

    def purge_stale(self, max_age_sec: float = 300) -> int:
        """Remove pending requests older than max_age_sec. Returns count."""
        now = time.time()
        stale = [
            rid for rid, req in self._pending.items()
            if req.status == "pending" and now - req.created_at > max_age_sec
        ]
        for rid in stale:
            req = self._pending.pop(rid, None)
            if req:
                req.status = "timeout"
                self._history.append(req)
        return len(stale)

    def stats(self) -> dict:
        return {
            "policy": self.policy.value,
            "pending": len(self._pending),
            "history": len(self._history),
            "approved": sum(1 for r in self._history if r.status == "approved"),
            "denied": sum(1 for r in self._history if r.status == "denied"),
        }


EventEmit = Callable[[SSEEvent], Awaitable[None]]


class ApprovalBridge:
    def __init__(self, manager: ApprovalManager, event_emit: EventEmit | None = None):
        self.manager = manager
        self._event_emit = event_emit

    def set_event_emit(self, event_emit: EventEmit) -> None:
        self._event_emit = event_emit

    async def request_command_approval(
        self,
        session_id: str,
        thread_id: str,
        command: str,
        risk: RiskLevel = RiskLevel.HIGH,
        description: str = "",
    ) -> ApprovalRequest:
        request = self.manager.create_request(
            "exec_command",
            cmd=command,
            risk=risk,
            description=description or command,
        )
        await self._emit(
            SSEEventType.EXEC_APPROVAL_REQUEST,
            session_id,
            thread_id,
            {
                "request_id": request.id,
                "command": command,
                "risk": request.risk_level.value,
                "description": request.description,
            },
        )
        return request

    async def request_file_approval(
        self,
        session_id: str,
        thread_id: str,
        file_path: str,
        description: str = "",
    ) -> ApprovalRequest:
        request = self.manager.create_request(
            "apply_patch",
            file_path=file_path,
            risk=RiskLevel.MEDIUM,
            description=description or file_path,
        )
        await self._emit(
            SSEEventType.APPLY_PATCH_APPROVAL_REQUEST,
            session_id,
            thread_id,
            {
                "request_id": request.id,
                "file_path": file_path,
                "risk": request.risk_level.value,
                "description": request.description,
            },
        )
        return request

    async def decide(self, request_id: str, action: str, session_id: str, thread_id: str) -> dict:
        request = self.manager._pending.get(request_id)
        request_type = request.type if request else ""
        ok = self.manager.approve(request_id) if action == "approve" else self.manager.deny(request_id)
        event_type = (
            SSEEventType.THREAD_FOLLOWER_FILE_APPROVAL_DECISION
            if request_type in ("apply_patch", "file_write", "file_delete")
            else SSEEventType.THREAD_FOLLOWER_COMMAND_APPROVAL_DECISION
        )
        await self._emit(
            event_type,
            session_id,
            thread_id,
            {"request_id": request_id, "decision": action, "ok": ok},
        )
        return {"request_id": request_id, "action": action, "ok": ok}

    async def _emit(self, event_type: SSEEventType, session_id: str, thread_id: str, data: dict) -> None:
        if self._event_emit is None:
            return
        await self._event_emit(SSEEvent(type=event_type, data=data, session_id=session_id, thread_id=thread_id))

approval_manager = ApprovalManager()
approval_bridge = ApprovalBridge(approval_manager, sse_bus.emit)
