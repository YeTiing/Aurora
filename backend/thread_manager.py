# Thread Management API — create/fork/list/read/pin/archive (对齐Codex)
from __future__ import annotations
import time, uuid, json
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ThreadInfo:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    workspace: str = ""
    model: str = ""
    reasoning_effort: str = "medium"
    status: str = "active"  # active / archived / pinned
    parent_id: str = ""
    agent_nickname: str = ""
    agent_role: str = ""
    thread_source: str = ""  # user / automation / fork
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    preview: str = ""
    metadata: dict = field(default_factory=dict)

class ThreadManager:
    """线程管理器 — create / fork / list / read / pin / archive / rename"""

    def __init__(self):
        self._threads: dict[str, ThreadInfo] = {}

    def create(self, title: str = "", workspace: str = "", model: str = "",
               reasoning_effort: str = "medium", thread_source: str = "user",
               agent_nickname: str = "", agent_role: str = "") -> ThreadInfo:
        t = ThreadInfo(
            title=title or "New Thread", workspace=workspace, model=model,
            reasoning_effort=reasoning_effort, thread_source=thread_source,
            agent_nickname=agent_nickname, agent_role=agent_role,
        )
        self._threads[t.id] = t
        return t

    def fork(self, thread_id: str, title: str = "") -> ThreadInfo | None:
        src = self._threads.get(thread_id)
        if not src: return None
        t = ThreadInfo(
            title=title or f"{src.title} (fork)", parent_id=thread_id,
            workspace=src.workspace, model=src.model,
            reasoning_effort=src.reasoning_effort,
            thread_source="fork",
            agent_nickname=src.agent_nickname, agent_role=src.agent_role,
        )
        self._threads[t.id] = t
        return t

    def list_threads(self, status: str = "", limit: int = 50) -> list[ThreadInfo]:
        threads = list(self._threads.values())
        if status:
            threads = [t for t in threads if t.status == status]
        threads.sort(key=lambda t: t.updated_at, reverse=True)
        return threads[:limit]

    def read(self, thread_id: str) -> ThreadInfo | None:
        return self._threads.get(thread_id)

    def set_pinned(self, thread_id: str, pinned: bool = True) -> bool:
        t = self._threads.get(thread_id)
        if not t: return False
        t.status = "pinned" if pinned else "active"
        t.updated_at = time.time()
        return True

    def set_archived(self, thread_id: str) -> bool:
        t = self._threads.get(thread_id)
        if not t: return False
        t.status = "archived"
        t.updated_at = time.time()
        return True

    def set_title(self, thread_id: str, title: str) -> bool:
        t = self._threads.get(thread_id)
        if not t: return False
        t.title = title
        t.updated_at = time.time()
        return True

    def stats(self) -> dict:
        statuses = {}
        for t in self._threads.values():
            statuses[t.status] = statuses.get(t.status, 0) + 1
        return {"total": len(self._threads), "by_status": statuses}

thread_manager = ThreadManager()