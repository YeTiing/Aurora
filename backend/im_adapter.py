"""IM Adapter Bridge — WebSocket session transport for messaging apps.

Connects Telegram/WeChat/Feishu/DingTalk bots to Aurora's chat engine
via WebSocket, enabling remote dialogue, permission approvals, and
project switching from IM clients.

Architecture:
  IM Bot → Adapter Process → WS Bridge → Aurora Server → Agent Loop
"""
from __future__ import annotations
import asyncio, json, time, uuid, threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class AdapterSession:
    session_id: str
    chat_id: str  # IM chat identifier
    adapter_type: str  # telegram, wechat, feishu, dingtalk
    workspace: str = "."
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class AdapterBridge:
    """WebSocket-based bridge between IM adapters and Aurora core."""

    def __init__(self, max_sessions: int = 100):
        self.sessions: dict[str, AdapterSession] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._max_sessions = max_sessions
        self._lock = threading.Lock()
        self._on_message: Optional[Callable] = None
        self._on_permission: Optional[Callable] = None

    # ——— Session management ———

    def create_session(self, chat_id: str, adapter_type: str,
                       workspace: str = ".", metadata: dict = None) -> AdapterSession:
        sid = f"{adapter_type}_{uuid.uuid4().hex[:8]}"
        session = AdapterSession(
            session_id=sid, chat_id=chat_id,
            adapter_type=adapter_type, workspace=workspace,
            metadata=metadata or {},
        )
        with self._lock:
            self.sessions[sid] = session
        return session

    def get_session(self, chat_id: str, adapter_type: str) -> Optional[AdapterSession]:
        for s in self.sessions.values():
            if s.chat_id == chat_id and s.adapter_type == adapter_type:
                return s
        return None

    def get_session_by_id(self, session_id: str) -> Optional[AdapterSession]:
        return self.sessions.get(session_id)

    # ——— Message routing ———

    async def send_to_agent(self, session_id: str, text: str,
                            attachments: list = None) -> dict:
        """Send a user message from IM to the agent for processing."""
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "session not found", "session_id": session_id}

        session.last_active = time.time()

        # Call the registered handler (set by API layer)
        if self._on_message:
            return await self._on_message(session, text, attachments or [])
        return {"error": "no message handler registered"}

    async def request_permission(self, session_id: str, tool: str,
                                  args: dict, preview: str = "") -> str:
        """Request user permission for a tool call via IM.
        Returns: 'allow', 'deny', or 'timeout'."""
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        req_id = uuid.uuid4().hex[:8]
        self._pending[req_id] = future

        if self._on_permission:
            await self._on_permission(session_id, req_id, tool, args, preview)

        try:
            result = await asyncio.wait_for(future, timeout=60.0)
            return result
        except asyncio.TimeoutError:
            return "timeout"

    def resolve_permission(self, req_id: str, decision: str):
        future = self._pending.pop(req_id, None)
        if future and not future.done():
            future.set_result(decision)

    # ——— Stats ———

    def stats(self) -> dict:
        with self._lock:
            return {
                "active_sessions": len(self.sessions),
                "pending_permissions": len(self._pending),
                "by_adapter": {
                    t: sum(1 for s in self.sessions.values() if s.adapter_type == t)
                    for t in set(s.adapter_type for s in self.sessions.values())
                },
            }


adapter_bridge = AdapterBridge()
