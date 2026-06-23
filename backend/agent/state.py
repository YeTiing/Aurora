# Agent 状态定义 — 完整的 TypedDict + dataclass 双模式
from __future__ import annotations
import json, time, uuid
from dataclasses import dataclass, field, asdict
from typing import Any, TypedDict, Literal, Optional

# ── 消息类型 ──
@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_openai(self) -> dict:
        d: dict = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str, tool_calls: list[dict] | None = None) -> "Message":
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def tool(cls, content: str, tool_call_id: str, name: str = "") -> "Message":
        return cls(role="tool", content=content, tool_call_id=tool_call_id, name=name)

# ── 计划步骤 ──
@dataclass
class PlanStep:
    step: int
    description: str
    status: Literal["pending", "in_progress", "completed", "failed", "skipped"] = "pending"
    tool: str | None = None
    estimated_turns: int = 1
    result: str | None = None
    started_at: float | None = None
    completed_at: float | None = None

    def start(self):
        self.status = "in_progress"
        self.started_at = time.time()

    def complete(self, result: str = ""):
        self.status = "completed"
        self.completed_at = time.time()
        self.result = result

    def fail(self, reason: str = ""):
        self.status = "failed"
        self.completed_at = time.time()
        self.result = reason

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanStep":
        return cls(**{k: d.get(k) for k in ["step", "description", "status", "tool", "estimated_turns", "result", "started_at", "completed_at"]})

# ── 工具调用 ──
@dataclass
class ToolInvocation:
    id: str
    name: str
    arguments: dict
    timestamp: float = field(default_factory=time.time)

@dataclass
class ToolResult:
    invocation_id: str
    name: str
    output: str
    success: bool
    error: str | None = None
    duration_ms: float = 0
    truncated: bool = False

# ── Agent 主状态 ──
class AgentStateDict(TypedDict, total=False):
    messages: list[dict]
    plan: list[dict]
    current_step: int
    tool_invocations: list[dict]
    tool_results: list[dict]
    empty_turns: int
    done: bool
    final_response: str
    diffs: list[str]
    error: str | None
    metadata: dict
    checkpoint_id: str
    session_id: str
    workspace: str

@dataclass
class AgentState:
    """Agent 完整状态 — 贯穿六步流水线"""
    messages: list[Message] = field(default_factory=list)
    plan: list[PlanStep] = field(default_factory=list)
    current_step: int = 0
    total_turns: int = 0
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    empty_turns: int = 0
    done: bool = False
    final_response: str = ""
    diffs: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict = field(default_factory=dict)
    checkpoint_id: str = ""
    session_id: str = ""
    workspace: str = "."
    sandbox_mode: str = "full-access"
    reasoning_effort: str = "medium"  # low/medium/high/xhigh

    def to_dict(self) -> AgentStateDict:
        return {
            "messages": [asdict(m) for m in self.messages],
            "plan": [p.to_dict() for p in self.plan],
            "current_step": self.current_step,
            "total_turns": self.total_turns,
            "tool_invocations": [asdict(t) for t in self.tool_invocations],
            "tool_results": [asdict(r) for r in self.tool_results],
            "empty_turns": self.empty_turns,
            "done": self.done,
            "final_response": self.final_response,
            "diffs": self.diffs,
            "error": self.error,
            "metadata": self.metadata,
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "workspace": self.workspace,
        }

    @classmethod
    def from_dict(cls, d: AgentStateDict) -> "AgentState":
        messages = [Message(**m) for m in d.get("messages", [])]
        plan = [PlanStep.from_dict(p) for p in d.get("plan", [])]
        invocations = [ToolInvocation(**t) for t in d.get("tool_invocations", [])]
        results = [ToolResult(**r) for r in d.get("tool_results", [])]
        return cls(
            messages=messages, plan=plan,
            current_step=d.get("current_step", 0),
            total_turns=d.get("total_turns", 0),
            tool_invocations=invocations, tool_results=results,
            empty_turns=d.get("empty_turns", 0),
            done=d.get("done", False),
            final_response=d.get("final_response", ""),
            diffs=d.get("diffs", []),
            error=d.get("error"),
            metadata=d.get("metadata", {}),
            checkpoint_id=d.get("checkpoint_id", ""),
            session_id=d.get("session_id", ""),
            workspace=d.get("workspace", "."),
        )

    def messages_as_openai(self) -> list[dict]:
        return [m.to_openai() for m in self.messages]

    def add_message(self, msg: Message):
        self.messages.append(msg)

    def current_plan_step(self) -> PlanStep | None:
        if 0 <= self.current_step < len(self.plan):
            return self.plan[self.current_step]
        return None

    def plan_progress(self) -> dict:
        total = len(self.plan)
        completed = sum(1 for p in self.plan if p.status == "completed")
        failed = sum(1 for p in self.plan if p.status == "failed")
        return {"total": total, "completed": completed, "failed": failed, "in_progress": total - completed - failed, "percentage": int(completed / max(total, 1) * 100)}

    def clone_for_checkpoint(self) -> "AgentState":
        """深拷贝用于快照存储"""
        return AgentState.from_dict(self.to_dict())