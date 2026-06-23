# Background task queue — asyncio-based with concurrency limit
from __future__ import annotations
import asyncio, time, uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

@dataclass
class TaskInfo:
    id: str
    name: str
    status: str  # pending, running, done, error
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None

class TaskQueue:
    """Simple asyncio task queue with max concurrency."""

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, TaskInfo] = {}
        self._futures: dict[str, asyncio.Task] = {}

    async def submit(self, fn: Callable[..., Coroutine], *args, name: str = "") -> str:
        """Submit an async callable to the queue. Returns task_id."""
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        info = TaskInfo(id=task_id, name=name or fn.__name__, status="pending")
        self._tasks[task_id] = info
        future = asyncio.ensure_future(self._run(task_id, fn, *args))
        self._futures[task_id] = future
        return task_id

    async def _run(self, task_id: str, fn: Callable[..., Coroutine], *args):
        info = self._tasks.get(task_id)
        if not info:
            return
        async with self._semaphore:
            info.status = "running"
            info.started_at = time.time()
            try:
                info.result = await fn(*args)
                info.status = "done"
            except Exception as e:
                info.status = "error"
                info.error = str(e)[:500]
            finally:
                info.finished_at = time.time()

    def status(self, task_id: str) -> dict | None:
        """Get status of a task."""
        info = self._tasks.get(task_id)
        if not info:
            return None
        return {
            "id": info.id, "name": info.name, "status": info.status,
            "created_at": info.created_at, "started_at": info.started_at,
            "finished_at": info.finished_at,
            "result": str(info.result)[:200] if info.result is not None else None,
            "error": info.error
        }

    def cancel(self, task_id: str) -> bool:
        """Cancel a pending or running task."""
        future = self._futures.get(task_id)
        if future and not future.done():
            future.cancel()
            info = self._tasks.get(task_id)
            if info:
                info.status = "error"
                info.error = "Cancelled"
            return True
        return False

    def list_all(self) -> list[dict]:
        return [{
            "id": t.id, "name": t.name, "status": t.status,
            "created_at": t.created_at
        } for t in self._tasks.values()]

    def stats(self) -> dict:
        total = len(self._tasks)
        by_status = {}
        for t in self._tasks.values():
            by_status[t.status] = by_status.get(t.status, 0) + 1
        return {"total": total, "by_status": by_status, "max_concurrent": self.max_concurrent}


task_queue = TaskQueue(max_concurrent=3)
