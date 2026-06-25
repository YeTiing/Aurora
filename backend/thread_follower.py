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
        model: str = "",
        effort: ReasoningEffort = "medium",
    ) -> dict:
        record = self._threads.get(thread_id)
        thread_settings = settings or ThreadSettings(model=model, reasoning_effort=effort)
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


    async def load_complete_history(self, thread_id: str) -> dict:
        """Load the complete message history for a thread."""
        record = self._require_thread(thread_id)
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_LOAD_COMPLETE_HISTORY,
            record,
            {"messages": record.messages, "summary": record.summary},
        )
        return {
            "thread_id": thread_id,
            "messages": record.messages,
            "summary": record.summary,
        }

    async def edit_last_user_message(self, thread_id: str, new_message: str) -> dict:
        """Edit the last user message in the thread."""
        record = self._require_thread(thread_id)
        for i in range(len(record.messages) - 1, -1, -1):
            if record.messages[i].get("role") == "user":
                record.messages[i]["content"] = new_message
                break
        self._threads = {**self._threads, thread_id: record}
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_EDIT_LAST_USER_TURN,
            record,
            {"new_message": new_message, "edited": True},
        )
        return {"thread_id": thread_id, "edited": True, "new_message": new_message}

    async def handle_command_approval(self, thread_id: str, decision: str) -> dict:
        """Submit a command approval decision (approved/denied)."""
        record = self._require_thread(thread_id)
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_COMMAND_APPROVAL_DECISION,
            record,
            {"decision": decision, "thread_id": thread_id},
        )
        return {"thread_id": thread_id, "decision": decision}

    async def handle_file_approval(self, thread_id: str, decision: str) -> dict:
        """Submit a file operation approval decision (approved/denied)."""
        record = self._require_thread(thread_id)
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_FILE_APPROVAL_DECISION,
            record,
            {"decision": decision, "thread_id": thread_id},
        )
        return {"thread_id": thread_id, "decision": decision}

    async def handle_permissions_escalation(self, thread_id: str, response: dict) -> dict:
        """Submit a response to a permissions escalation request."""
        record = self._require_thread(thread_id)
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_PERMISSIONS_REQUEST_APPROVAL_RESPONSE,
            record,
            {"response": response, "thread_id": thread_id},
        )
        return {"thread_id": thread_id, "response": response}

    async def submit_user_input(self, thread_id: str, input_data: str | dict) -> dict:
        """Submit user input (form, text, or structured data) to the thread."""
        record = self._require_thread(thread_id)
        if isinstance(input_data, dict):
            input_data = input_data
        record.messages = [*record.messages, {"role": "user", "content": input_data}]
        self._threads = {**self._threads, thread_id: record}
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_SUBMIT_USER_INPUT,
            record,
            {"input_data": input_data, "thread_id": thread_id},
        )
        return {"thread_id": thread_id, "submitted": True, "input_data": input_data}

    async def submit_mcp_elicitation(self, thread_id: str, response: str | dict) -> dict:
        """Submit a response to an MCP server elicitation request."""
        record = self._require_thread(thread_id)
        await self._emit(
            SSEEventType.THREAD_FOLLOWER_SUBMIT_MCP_SERVER_ELICITATION_RESPONSE,
            record,
            {"response": response, "thread_id": thread_id},
        )
        return {"thread_id": thread_id, "submitted": True, "response": response}


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
