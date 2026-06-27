# Thread Management API — create/fork/list/read/pin/archive
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
    """Thread manager backed by SQLite; in-memory fallback if DB unavailable."""

    def __init__(self):
        self._threads: dict[str, ThreadInfo] = {}
        try:
            from backend.sqlite_persistence import ThreadsDB
            self._db = ThreadsDB()
            self._use_db = True
        except Exception:
            self._db = None
            self._use_db = False

    def _persist(self, t: ThreadInfo):
        if self._use_db and self._db:
            try:
                self._db.update(t.id, title=t.title, workspace=t.workspace,
                                agent_nickname=t.agent_nickname, agent_role=t.agent_role,
                                model=t.model, reasoning_effort=t.reasoning_effort,
                                thread_source=t.thread_source, status=t.status,
                                updated_at=t.updated_at, metadata=json.dumps(t.metadata, default=str))
            except Exception:
                pass

    def create(self, title: str = "", workspace: str = "", model: str = "",
               reasoning_effort: str = "medium", thread_source: str = "user",
               agent_nickname: str = "", agent_role: str = "") -> ThreadInfo:
        t = ThreadInfo(
            title=title or "New Thread", workspace=workspace, model=model,
            reasoning_effort=reasoning_effort, thread_source=thread_source,
            agent_nickname=agent_nickname, agent_role=agent_role,
        )
        self._threads[t.id] = t
        if self._use_db and self._db:
            try:
                self._db.create(id=t.id, title=t.title, workspace=t.workspace,
                                agent_nickname=t.agent_nickname, agent_role=t.agent_role,
                                model=t.model, reasoning_effort=t.reasoning_effort,
                                thread_source=t.thread_source, status=t.status)
            except Exception:
                pass
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
        if self._use_db and self._db:
            try:
                self._db.create(id=t.id, title=t.title, workspace=t.workspace,
                                agent_nickname=t.agent_nickname, agent_role=t.agent_role,
                                model=t.model, reasoning_effort=t.reasoning_effort,
                                thread_source=t.thread_source)
            except Exception:
                pass
        return t

    def list_threads(self, status: str = "", limit: int = 50) -> list[ThreadInfo]:
        if self._use_db and self._db and not status:
            try:
                rows = self._db.list_recent(limit)
                result = []
                for r in rows:
                    ti = ThreadInfo(
                        id=r.get("id",""), title=r.get("title",""),
                        workspace=r.get("workspace",""), model=r.get("model",""),
                        reasoning_effort=r.get("reasoning_effort","medium"),
                        status=r.get("status","active"), agent_nickname=r.get("agent_nickname",""),
                        agent_role=r.get("agent_role",""), thread_source=r.get("thread_source",""),
                        created_at=r.get("created_at",time.time()),
                        updated_at=r.get("updated_at",time.time()),
                        preview=r.get("preview",""),
                    )
                    self._threads[ti.id] = ti
                    result.append(ti)
                return result
            except Exception:
                pass
        threads = list(self._threads.values())
        if status:
            threads = [t for t in threads if t.status == status]
        threads.sort(key=lambda t: t.updated_at, reverse=True)
        return threads[:limit]

    def read(self, thread_id: str) -> ThreadInfo | None:
        t = self._threads.get(thread_id)
        if t:
            return t
        if self._use_db and self._db:
            try:
                row = self._db.get(thread_id)
                if row:
                    t = ThreadInfo(
                        id=row.get("id",""), title=row.get("title",""),
                        workspace=row.get("workspace",""), model=row.get("model",""),
                        reasoning_effort=row.get("reasoning_effort","medium"),
                        status=row.get("status","active"), agent_nickname=row.get("agent_nickname",""),
                        agent_role=row.get("agent_role",""), thread_source=row.get("thread_source",""),
                        created_at=row.get("created_at",time.time()),
                        updated_at=row.get("updated_at",time.time()),
                        preview=row.get("preview",""),
                    )
                    self._threads[t.id] = t
                    return t
            except Exception:
                pass
        return None

    def set_pinned(self, thread_id: str, pinned: bool = True) -> bool:
        t = self._threads.get(thread_id) or self.read(thread_id)
        if not t: return False
        t.status = "pinned" if pinned else "active"
        t.updated_at = time.time()
        self._persist(t)
        return True

    def set_archived(self, thread_id: str) -> bool:
        t = self._threads.get(thread_id) or self.read(thread_id)
        if not t: return False
        t.status = "archived"
        t.updated_at = time.time()
        self._persist(t)
        return True

    def set_title(self, thread_id: str, title: str) -> bool:
        t = self._threads.get(thread_id) or self.read(thread_id)
        if not t: return False
        t.title = title
        t.updated_at = time.time()
        self._persist(t)
        return True

    def stats(self) -> dict:
        statuses = {}
        for t in self._threads.values():
            statuses[t.status] = statuses.get(t.status, 0) + 1
        return {"total": len(self._threads), "by_status": statuses}


thread_manager = ThreadManager()