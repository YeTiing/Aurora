# -*- coding: utf-8 -*-
"""Background Task Monitor — watches file changes, git PRs, issues.

Port of cc-haha's MonitorTool + scheduled tasks system.
Watches configured paths/URLs and notifies agent when changes are detected.
Supports: file watcher, git diff poller, PR status poller.
"""

from __future__ import annotations
import asyncio, logging, os, time, hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("aurora.monitor")


@dataclass
class WatchTarget:
    id: str = ""
    name: str = ""
    path: str = ""            # File path or URL
    watch_type: str = "file"  # file | git | pr | url
    interval_sec: int = 60
    last_hash: str = ""
    last_check: float = 0.0
    change_count: int = 0
    enabled: bool = True
    on_change: Callable | None = None


@dataclass
class ChangeEvent:
    target_id: str = ""
    target_name: str = ""
    change_type: str = "modified"  # created | modified | deleted
    detail: str = ""
    timestamp: float = field(default_factory=time.time)


class BackgroundMonitor:
    """Watches targets and emits change events."""

    def __init__(self):
        self._targets: dict[str, WatchTarget] = {}
        self._events: list[ChangeEvent] = []
        self._events_lock = threading.Lock()
        self._running = False
        self._task: asyncio.Task | None = None
        self._notify_callback: Callable | None = None

    def add_target(self, name: str, path: str, watch_type: str = "file",
                   interval_sec: int = 60, on_change: Callable | None = None) -> str:
        """Add a watch target. Returns target ID."""
        tid = hashlib.md5(f"{name}:{path}".encode()).hexdigest()[:12]
        target = WatchTarget(
            id=tid, name=name, path=path, watch_type=watch_type,
            interval_sec=interval_sec, on_change=on_change,
        )
        if watch_type == "file":
            target.last_hash = self._file_hash(path)
        elif watch_type == "git":
            target.last_hash = self._git_hash(path)
        self._targets[tid] = target
        logger.info(f"Monitor: watching '{name}' ({watch_type}) every {interval_sec}s")
        return tid

    def remove_target(self, target_id: str) -> bool:
        return self._targets.pop(target_id, None) is not None

    def set_notify(self, callback: Callable) -> None:
        """Set callback for change notifications."""
        self._notify_callback = callback

    def get_events(self, since: float = 0) -> list[ChangeEvent]:
        """Get change events since timestamp."""
        with self._events_lock:
            if since <= 0:
                return list(self._events[-50:])
            return [e for e in self._events if e.timestamp > since]

    def get_targets(self) -> list[dict]:
        """List all watch targets."""
        return [
            {
                "id": t.id, "name": t.name, "path": t.path,
                "type": t.watch_type, "interval_sec": t.interval_sec,
                "changes": t.change_count, "last_check": t.last_check,
                "enabled": t.enabled,
            }
            for t in self._targets.values()
        ]

    # ── Polling loop ────────────────────────────────────────────

    async def start(self, default_interval: int = 30) -> None:
        """Start background polling."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(default_interval))
        logger.info(f"Monitor started: {len(self._targets)} targets")

    async def stop(self) -> None:
        """Stop background polling."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Monitor stopped")

    async def _poll_loop(self, default_interval: int) -> None:
        """Main polling loop."""
        while self._running:
            try:
                for tid, target in list(self._targets.items()):
                    if not target.enabled:
                        continue
                    if time.time() - target.last_check < target.interval_sec:
                        continue

                    target.last_check = time.time()
                    new_hash = ""

                    if target.watch_type == "file":
                        new_hash = self._file_hash(target.path)
                    elif target.watch_type == "git":
                        new_hash = self._git_hash(target.path)

                    if new_hash and new_hash != target.last_hash:
                        prev = target.last_hash
                        target.last_hash = new_hash
                        target.change_count += 1

                        change_type = "modified" if prev else "created"
                        event = ChangeEvent(
                            target_id=tid, target_name=target.name,
                            change_type=change_type, detail=f"hash: {prev[:8]} -> {new_hash[:8]}",
                        )
                        with self._events_lock:
                            self._events.append(event)
                            if len(self._events) > 200:
                                self._events = self._events[-200:]

                        logger.info(f"Monitor: {target.name} changed ({change_type})")

                        if target.on_change:
                            try:
                                result = target.on_change(target, event)
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception as e:
                                logger.debug(f"Monitor on_change error: {e}")

                        if self._notify_callback:
                            try:
                                result = self._notify_callback(event)
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception as e:
                                logger.debug(f"Monitor notify error: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Monitor poll error: {e}")

            await asyncio.sleep(default_interval)

    async def check_now(self) -> list[ChangeEvent]:
        """Force immediate check of all targets."""
        new_events = []
        for tid, target in self._targets.items():
            if not target.enabled:
                continue
            new_hash = ""
            if target.watch_type == "file":
                new_hash = self._file_hash(target.path)
            elif target.watch_type == "git":
                new_hash = self._git_hash(target.path)
            if new_hash and new_hash != target.last_hash:
                target.last_hash = new_hash
                target.change_count += 1
                event = ChangeEvent(target_id=tid, target_name=target.name, change_type="modified", detail=f"manual check")
                self._events.append(event)
                new_events.append(event)
        return new_events

    # ── Hash helpers ────────────────────────────────────────────

    @staticmethod
    def _file_hash(path: str) -> str:
        try:
            stat = os.stat(path)
            # Include mtime_ns for sub-second precision, fallback to mtime on older Python
            mtime = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))
            return hashlib.md5(f"{mtime}:{stat.st_size}".encode()).hexdigest()
        except OSError:
            return ""

    @staticmethod
    def _git_hash(path: str) -> str:
        import subprocess
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%H", "--", "."],
                capture_output=True, text=True, cwd=path, timeout=10
            )
            return result.stdout.strip()
        except Exception:
            return ""


_monitor: Optional[BackgroundMonitor] = None

def get_monitor() -> BackgroundMonitor:
    global _monitor
    if _monitor is None:
        _monitor = BackgroundMonitor()
    return _monitor
