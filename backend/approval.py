# Approval 审批系统 — exec_approval / patch_approval / request_user_input
from __future__ import annotations
import time, uuid, asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

class ApprovalPolicy(Enum):
    NEVER = "never"           # 自动执行，无需审批
    ON_FAILURE = "on-failure" # 仅在失败时提示
    ON_REQUEST = "on-request" # 每次操作都请求确认
    UNTRUSTED = "untrusted"   # 严格模式，所有操作需确认

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

    def __init__(self, policy: ApprovalPolicy = ApprovalPolicy.NEVER):
        self.policy = policy
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []
        self._callbacks: dict[str, Callable] = {}

    def set_policy(self, policy: ApprovalPolicy):
        self.policy = policy

    def needs_approval(self, risk: RiskLevel, tool_name: str = "") -> bool:
        if self.policy == ApprovalPolicy.NEVER:
            return False
        if self.policy == ApprovalPolicy.ON_FAILURE:
            return False  # 只在失败时触发
        if self.policy == ApprovalPolicy.UNTRUSTED:
            return True
        if self.policy == ApprovalPolicy.ON_REQUEST:
            return risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        return False

    def assess_risk(self, tool_name: str, arguments: dict) -> RiskLevel:
        if tool_name in ("code_search", "list_files", "view_image", "todo_write", "plan_update"):
            return RiskLevel.LOW
        if tool_name in ("file_rw", "apply_patch", "git_ops"):
            cmd = str(arguments)
            if "write" in cmd or "patch" in cmd or "commit" in cmd or "push" in cmd:
                return RiskLevel.MEDIUM
            return RiskLevel.LOW
        if tool_name in ("web_fetch", "web_search", "send_message"):
            return RiskLevel.MEDIUM
        if tool_name in ("shell_command", "code_exec", "computer_use"):
            cmd = str(arguments).lower()
            if any(d in cmd for d in ("rm -rf", "format", "del /", "rd /", "drop", "shutdown")):
                return RiskLevel.CRITICAL
            return RiskLevel.HIGH
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
            return True
        return False

    def deny(self, request_id: str) -> bool:
        req = self._pending.pop(request_id, None)
        if req:
            req.status = "denied"
            self._history.append(req)
            return True
        return False

    def get_pending(self) -> list[ApprovalRequest]:
        return [r for r in self._pending.values() if r.status == "pending"]

    def stats(self) -> dict:
        return {
            "policy": self.policy.value,
            "pending": len(self._pending),
            "history": len(self._history),
            "approved": sum(1 for r in self._history if r.status == "approved"),
            "denied": sum(1 for r in self._history if r.status == "denied"),
        }

approval_manager = ApprovalManager()