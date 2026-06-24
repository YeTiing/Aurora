from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

from backend.agent.sse_events import SSEEvent, SSEEventType
from backend.context.collapse import ContextCollapser

ReasoningEffort = Literal["low", "medium", "high", "xhigh"]
SandboxPolicy = Literal["read-only", "workspace-write", "danger-full-access", "full-access", "workspace-only"]
ApprovalMode = Literal["never", "on-failure", "on-request", "untrusted"]
ThreadStatus = Literal["idle", "running", "interrupted", "completed"]


@dataclass(frozen=True)
class ThreadSettings:
    model: str = ""
    reasoning_effort: ReasoningEffort = "medium"
    sandbox_policy: SandboxPolicy = "workspace-write"
    approval_mode: ApprovalMode = "on-request"

    def to_dict(self) -> dict[str, str]:
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "sandbox_policy": self.sandbox_policy,
            "approval_mode": self.approval_mode,
        }


@dataclass
class ThreadRecord:
    thread_id: str
    session_id: str
    messages: list[dict[str, str]] = field(default_factory=list)
    settings: ThreadSettings = field(default_factory=ThreadSettings)
    queued_followups: list[str] = field(default_factory=list)
    status: ThreadStatus = "idle"
    summary: str = ""


EventEmit = Callable[[SSEEvent], Awaitable[None]]


class ThreadFollower:
    def __init__(self, event_emit: EventEmit | None = None, collapser: ContextCollapser | None = None):
        self._threads: dict[str, ThreadRecord] = {}
        self._event_emit = event_emit
        self._collapser = collapser or ContextCollapser()

    def set_event_emit(self, event_emit: EventEmit) -> None:
        self._event_emit = event_emit

    async def start_turn(
        self,
        thread_id: str,
        session_id: str,
        message: str,
        settings: ThreadSettings | None = None,
    ) -> dict:
        record = self._threads.get(thread_id)
        thread_settings = settings or ThreadSettings()
        if record is None:
            record = ThreadRecord(thread_id=thread_id, session_id=session_id, settings=thread_settings)
        record.status = "running"
        record.session_id = session_id
        record.settings = thread_settings
        record.messages = [*record.messages, {"role": "user", "content": message}]
        self._threads = {**self._threads, thread_id: record}
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_START_TURN,
            record,
            {"message": message, "settings": thread_settings.to_dict()},
        )
        return {
            "thread_id": thread_id,
            "session_id": session_id,
            "status": record.status,
            "settings": thread_settings.to_dict(),
        }

    async def steer_turn(self, thread_id: str, instruction: str) -> dict:
        record = self._require_thread(thread_id)
        record.messages = [*record.messages, {"role": "system", "content": instruction}]
        self._threads = {**self._threads, thread_id: record}
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_STEER_TURN,
            record,
            {"instruction": instruction, "accepted": True},
        )
        return {"thread_id": thread_id, "accepted": True}

    async def interrupt_turn(self, thread_id: str, reason: str = "") -> dict:
        record = self._require_thread(thread_id)
        record.status = "interrupted"
        self._threads = {**self._threads, thread_id: record}
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_INTERRUPT_TURN,
            record,
            {"reason": reason, "interrupted": True},
        )
        return {"thread_id": thread_id, "interrupted": True, "reason": reason}

    async def compact_thread(self, thread_id: str, token_usage_ratio: float) -> dict:
        record = self._require_thread(thread_id)
        if token_usage_ratio < 0.85:
            await self._emit(
                SSEEventType.THREAD_FOLLOWER_COMPACT_THREAD,
                record,
                {"compacted": False, "summary": record.summary, "token_usage_ratio": token_usage_ratio},
            )
            return {"thread_id": thread_id, "compacted": False, "summary": record.summary}

        collapsed_messages, summary = self._collapser.collapse(record.messages, keep_last=4)
        record.messages = collapsed_messages
        record.summary = summary or record.summary or "No previous turns required compaction."
        self._threads = {**self._threads, thread_id: record}
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_COMPACT_THREAD,
            record,
            {
                "compacted": True,
                "summary": record.summary,
                "token_usage_ratio": token_usage_ratio,
            },
        )
        return {"thread_id": thread_id, "compacted": True, "summary": record.summary}

    async def set_queued_followups(self, thread_id: str, followups: list[str]) -> dict:
        record = self._require_thread(thread_id)
        record.queued_followups = [*followups]
        self._threads = {**self._threads, thread_id: record}
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_SET_QUEUED_FOLLOWUPS_STATE,
            record,
            {"queued_followups": record.queued_followups},
        )
        return {"thread_id": thread_id, "queued_followups": record.queued_followups}

    async def update_thread_settings(self, thread_id: str, settings: ThreadSettings) -> dict:
        record = self._require_thread(thread_id)
        record.settings = settings
        self._threads = {**self._threads, thread_id: record}
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_UPDATE_THREAD_SETTINGS,
            record,
            {"settings": settings.to_dict()},
        )
        return {"thread_id": thread_id, "settings": settings.to_dict()}

    def get_thread(self, thread_id: str) -> ThreadRecord:
        return self._require_thread(thread_id)

    def _require_thread(self, thread_id: str) -> ThreadRecord:
        record = self._threads.get(thread_id)
        if record is None:
            raise KeyError(f"Unknown thread: {thread_id}")
        return record

    async def _emit(self, event_type: SSEEventType, record: ThreadRecord, data: dict) -> None:
        if self._event_emit is None:
            return
        await self._event_emit(
            SSEEvent(
                type=event_type,
                data=data,
                session_id=record.session_id,
                thread_id=record.thread_id,
            )
        )
