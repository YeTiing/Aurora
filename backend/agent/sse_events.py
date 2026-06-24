# Aurora SSE event system — Codex-compatible events
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from enum import Enum
import json, time, uuid


class SSEEventType(Enum):
    """Codex SSE event type registry."""

    # Thread Follower Controls
    THREAD_FOLLOWER_START_TURN = "codex/event/thread_follower_start_turn"
    THREAD_FOLLOWER_STEER_TURN = "codex/event/thread_follower_steer_turn"
    THREAD_FOLLOWER_INTERRUPT_TURN = "codex/event/thread_follower_interrupt_turn"
    THREAD_FOLLOWER_EDIT_LAST_USER_TURN = "codex/event/thread_follower_edit_last_user_turn"
    THREAD_FOLLOWER_COMPACT_THREAD = "codex/event/thread_follower_compact_thread"
    THREAD_FOLLOWER_LOAD_COMPLETE_HISTORY = "codex/event/thread_follower_load_complete_history"
    THREAD_FOLLOWER_COMMAND_APPROVAL_DECISION = "codex/event/thread_follower_command_approval_decision"
    THREAD_FOLLOWER_FILE_APPROVAL_DECISION = "codex/event/thread_follower_file_approval_decision"
    THREAD_FOLLOWER_PERMISSIONS_REQUEST_APPROVAL_RESPONSE = "codex/event/thread_follower_permissions_request_approval_response"
    THREAD_FOLLOWER_SUBMIT_USER_INPUT = "codex/event/thread_follower_submit_user_input"
    THREAD_FOLLOWER_SUBMIT_MCP_SERVER_ELICITATION_RESPONSE = "codex/event/thread_follower_submit_mcp_server_elicitation_response"
    THREAD_FOLLOWER_SET_QUEUED_FOLLOWUPS_STATE = "codex/event/thread_follower_set_queued_followups_state"
    THREAD_FOLLOWER_UPDATE_THREAD_SETTINGS = "codex/event/thread_follower_update_thread_settings"

    # Session / Task Lifecycle
    SESSION_CONFIGURED = "codex/event/session_configured"
    TASK_STARTED = "codex/event/task_started"
    TASK_COMPLETE = "codex/event/task_complete"
    TURN_ABORTED = "codex/event/turn_aborted"
    TURN_DIFF = "codex/event/turn_diff"
    UNDO_STARTED = "codex/event/undo_started"
    UNDO_COMPLETED = "codex/event/undo_completed"
    ERROR = "codex/event/error"
    STREAM_ERROR = "codex/event/stream_error"
    WARNING = "codex/event/warning"
    SHUTDOWN_COMPLETE = "codex/event/shutdown_complete"

    # Agent Reasoning / Messages
    AGENT_REASONING = "codex/event/agent_reasoning"
    AGENT_REASONING_DELTA = "codex/event/agent_reasoning_delta"
    AGENT_REASONING_RAW_CONTENT = "codex/event/agent_reasoning_raw_content"
    AGENT_REASONING_RAW_CONTENT_DELTA = "codex/event/agent_reasoning_raw_content_delta"
    AGENT_REASONING_SECTION_BREAK = "codex/event/agent_reasoning_section_break"
    AGENT_MESSAGE = "codex/event/agent_message"
    AGENT_MESSAGE_DELTA = "codex/event/agent_message_delta"
    AGENT_MESSAGE_CONTENT_DELTA = "codex/event/agent_message_content_delta"
    RAW_RESPONSE_ITEM = "codex/event/raw_response_item"
    REASONING_CONTENT_DELTA = "codex/event/reasoning_content_delta"
    REASONING_RAW_CONTENT_DELTA = "codex/event/reasoning_raw_content_delta"

    # Tool Calls (MCP + Exec)
    MCP_TOOL_CALL_BEGIN = "codex/event/mcp_tool_call_begin"
    MCP_TOOL_CALL_END = "codex/event/mcp_tool_call_end"
    MCP_STARTUP_UPDATE = "codex/event/mcp_startup_update"
    MCP_LIST_TOOLS_RESPONSE = "codex/event/mcp_list_tools_response"
    EXEC_COMMAND_BEGIN = "codex/event/exec_command_begin"
    EXEC_COMMAND_END = "codex/event/exec_command_end"
    EXEC_COMMAND_OUTPUT_DELTA = "codex/event/exec_command_output_delta"
    EXEC_APPROVAL_REQUEST = "codex/event/exec_approval_request"
    APPLY_PATCH_APPROVAL_REQUEST = "codex/event/apply_patch_approval_request"
    PATCH_APPLY_BEGIN = "codex/event/patch_apply_begin"
    PATCH_APPLY_END = "codex/event/patch_apply_end"

    # Plan / Review
    PLAN_DELTA = "codex/event/plan_delta"
    PLAN_UPDATE = "codex/event/plan_update"
    ENTERED_REVIEW_MODE = "codex/event/entered_review_mode"
    EXITED_REVIEW_MODE = "codex/event/exited_review_mode"
    ITEM_STARTED = "codex/event/item_started"
    ITEM_COMPLETED = "codex/event/item_completed"

    # Skills / Plugins
    LIST_SKILLS_RESPONSE = "codex/event/list_skills_response"
    LIST_REMOTE_SKILLS_RESPONSE = "codex/event/list_remote_skills_response"
    LIST_CUSTOM_PROMPTS_RESPONSE = "codex/event/list_custom_prompts_response"
    REMOTE_SKILL_DOWNLOADED = "codex/event/remote_skill_downloaded"

    # Other
    WEB_SEARCH_BEGIN = "codex/event/web_search_begin"
    WEB_SEARCH_END = "codex/event/web_search_end"
    VIEW_IMAGE_TOOL_CALL = "codex/event/view_image_tool_call"
    BACKGROUND_EVENT = "codex/event/background_event"
    USER_MESSAGE = "codex/event/user_message"
    GET_HISTORY_ENTRY_RESPONSE = "codex/event/get_history_entry_response"


@dataclass
class SSEEvent:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: SSEEventType = SSEEventType.BACKGROUND_EVENT
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""
    thread_id: str = ""

    def to_sse(self) -> str:
        """Format as SSE wire protocol"""
        d = {
            "id": self.id, "type": self.type.value, "data": self.data,
            "timestamp": self.timestamp, "session_id": self.session_id,
            "thread_id": self.thread_id,
        }
        return f"event: {self.type.value}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type.value, "data": self.data,
            "timestamp": self.timestamp, "session_id": self.session_id,
            "thread_id": self.thread_id,
        }


class SSEEventBus:
    """Event bus with subscriber pattern and history"""

    def __init__(self):
        self._subscribers: dict[str, list] = {}
        self._history: list[SSEEvent] = []
        self._max_history = 500

    def subscribe(self, session_id: str, callback):
        self._subscribers.setdefault(session_id, []).append(callback)

    def unsubscribe(self, session_id: str, callback):
        if session_id in self._subscribers:
            self._subscribers[session_id] = [
                cb for cb in self._subscribers[session_id] if cb != callback
            ]

    async def emit(self, event: SSEEvent):
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        for sid in (event.session_id, "*"):
            if sid in self._subscribers:
                for cb in self._subscribers[sid]:
                    try:
                        await cb(event)
                    except:
                        pass

    def get_history(self, session_id: str = "", limit: int = 50) -> list[dict]:
        events = self._history
        if session_id:
            events = [e for e in events if e.session_id == session_id or e.session_id == ""]
        return [e.to_dict() for e in events[-limit:]]

    # ── Convenience emitters ──

    async def thread_follower_start_turn(self, session_id: str, thread_id: str, settings: dict):
        await self.emit(SSEEvent(type=SSEEventType.THREAD_FOLLOWER_START_TURN,
            data={"settings": settings}, session_id=session_id, thread_id=thread_id))

    async def thread_follower_steer_turn(self, session_id: str, thread_id: str, instruction: str):
        await self.emit(SSEEvent(type=SSEEventType.THREAD_FOLLOWER_STEER_TURN,
            data={"instruction": instruction}, session_id=session_id, thread_id=thread_id))

    async def thread_follower_interrupt_turn(self, session_id: str, thread_id: str, reason: str):
        await self.emit(SSEEvent(type=SSEEventType.THREAD_FOLLOWER_INTERRUPT_TURN,
            data={"reason": reason}, session_id=session_id, thread_id=thread_id))

    async def thread_follower_compact_thread(self, session_id: str, thread_id: str, summary: str):
        await self.emit(SSEEvent(type=SSEEventType.THREAD_FOLLOWER_COMPACT_THREAD,
            data={"summary": summary}, session_id=session_id, thread_id=thread_id))

    async def session_configured(self, session_id: str, **kwargs):
        await self.emit(SSEEvent(type=SSEEventType.SESSION_CONFIGURED,
            data=kwargs, session_id=session_id))

    async def task_started(self, session_id: str, task: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.TASK_STARTED,
            data={"task": task}, session_id=session_id, thread_id=thread_id))

    async def task_complete(self, session_id: str, result: str = "", thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.TASK_COMPLETE,
            data={"result": result[:500]}, session_id=session_id, thread_id=thread_id))

    async def turn_aborted(self, session_id: str, reason: str = "", thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.TURN_ABORTED,
            data={"reason": reason}, session_id=session_id, thread_id=thread_id))

    async def agent_reasoning(self, session_id: str, text: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.AGENT_REASONING,
            data={"text": text}, session_id=session_id, thread_id=thread_id))

    async def agent_reasoning_delta(self, session_id: str, delta: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.AGENT_REASONING_DELTA,
            data={"delta": delta}, session_id=session_id, thread_id=thread_id))

    async def agent_reasoning_section_break(self, session_id: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.AGENT_REASONING_SECTION_BREAK,
            data={}, session_id=session_id, thread_id=thread_id))

    async def agent_message(self, session_id: str, message: str, phase: str = None, memory_citation: str = None, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.AGENT_MESSAGE,
            data={"message": message, "phase": phase, "memory_citation": memory_citation},
            session_id=session_id, thread_id=thread_id))

    async def agent_message_delta(self, session_id: str, delta: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.AGENT_MESSAGE_DELTA,
            data={"delta": delta}, session_id=session_id, thread_id=thread_id))

    async def agent_message_content_delta(self, session_id: str, delta: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.AGENT_MESSAGE_CONTENT_DELTA,
            data={"delta": delta}, session_id=session_id, thread_id=thread_id))

    async def tool_call_begin(self, session_id: str, tool_name: str, tool_id: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.EXEC_COMMAND_BEGIN,
            data={"tool": tool_name, "tool_call_id": tool_id},
            session_id=session_id, thread_id=thread_id))

    async def tool_call_end(self, session_id: str, tool_name: str, tool_id: str, success: bool, output: str = "", thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.EXEC_COMMAND_END,
            data={"tool": tool_name, "tool_call_id": tool_id, "success": success, "output": output[:2000]},
            session_id=session_id, thread_id=thread_id))

    async def mcp_tool_call_begin(self, session_id: str, server: str, tool: str, call_id: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.MCP_TOOL_CALL_BEGIN,
            data={"server": server, "tool": tool, "call_id": call_id},
            session_id=session_id, thread_id=thread_id))

    async def mcp_tool_call_end(self, session_id: str, server: str, tool: str, call_id: str, success: bool, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.MCP_TOOL_CALL_END,
            data={"server": server, "tool": tool, "call_id": call_id, "success": success},
            session_id=session_id, thread_id=thread_id))

    async def plan_update(self, session_id: str, plan: list[dict], thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.PLAN_UPDATE,
            data={"plan": plan}, session_id=session_id, thread_id=thread_id))

    async def plan_delta(self, session_id: str, delta: dict, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.PLAN_DELTA,
            data={"delta": delta}, session_id=session_id, thread_id=thread_id))

    async def item_started(self, session_id: str, item: dict, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.ITEM_STARTED,
            data={"item": item}, session_id=session_id, thread_id=thread_id))

    async def item_completed(self, session_id: str, item: dict, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.ITEM_COMPLETED,
            data={"item": item}, session_id=session_id, thread_id=thread_id))

    async def error(self, session_id: str, error: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.ERROR,
            data={"error": error[:1000]}, session_id=session_id, thread_id=thread_id))

    async def warning(self, session_id: str, warning: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.WARNING,
            data={"warning": warning[:500]}, session_id=session_id, thread_id=thread_id))

    async def user_message(self, session_id: str, message: str, client_id: str = "", thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.USER_MESSAGE,
            data={"message": message, "client_id": client_id},
            session_id=session_id, thread_id=thread_id))

    async def web_search_begin(self, session_id: str, query: str, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.WEB_SEARCH_BEGIN,
            data={"query": query}, session_id=session_id, thread_id=thread_id))

    async def web_search_end(self, session_id: str, results_count: int, thread_id: str = ""):
        await self.emit(SSEEvent(type=SSEEventType.WEB_SEARCH_END,
            data={"results_count": results_count}, session_id=session_id, thread_id=thread_id))


sse_bus = SSEEventBus()
