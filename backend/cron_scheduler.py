"""
Aurora Cron — Natural-language scheduled tasks with background ticker.

Tasks stored in .aurora/cron.json. Background thread checks every 60s.
When a task fires, it gets pushed into a queue for the agent to consume.

Usage:
    Agent: "每天早上8点提醒我站会" → creates cron task
    API: GET/POST/DELETE /cron
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CRON_DIR = Path(os.environ.get("AURORA_HOME", ".aurora"))
CRON_FILE = CRON_DIR / "cron.json"

# Simple cron-like schedule parser
# Supports: "every N minutes/hours", "at HH:MM", "daily at HH:MM"
import re as _regex


def parse_schedule(text: str) -> tuple[int, str]:
    """Parse human schedule text → (interval_seconds, description).
    
    Returns (0, error_msg) on failure.
    """
    text = text.lower().strip()
    
    # "every N minutes/seconds/hours"
    m = _regex.match(r"every\s+(\d+)\s*(minute|minutes|min|sec|second|seconds|hour|hours|h|day|days|d)", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit in ("sec", "second", "seconds"): return n, f"Every {n}s"
        if unit in ("minute", "minutes", "min"): return n * 60, f"Every {n}min"
        if unit in ("hour", "hours", "h"): return n * 3600, f"Every {n}h"
        if unit in ("day", "days", "d"): return n * 86400, f"Every {n}d"
    
    # "at HH:MM" or "daily at HH:MM"
    m = _regex.match(r"(daily\s+)?at\s+(\d{1,2}):(\d{2})", text)
    if m:
        h, mm = int(m.group(2)), int(m.group(3))
        target_sec = h * 3600 + mm * 60
        now = time.localtime()
        now_sec = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
        if target_sec > now_sec:
            delay = target_sec - now_sec
        else:
            delay = 86400 - (now_sec - target_sec)
        return delay, f"Daily at {h:02d}:{mm:02d}"
    
    return 0, f"Cannot parse schedule: {text}"

@dataclass
class CronTask:
    name: str
    schedule_text: str       # Human readable
    prompt: str               # What to inject when firing
    interval_seconds: int = 0
    last_run: float = 0.0
    next_run: float = 0.0
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    run_count: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "schedule": self.schedule_text,
            "prompt": self.prompt[:200],
            "interval_seconds": self.interval_seconds,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "enabled": self.enabled,
            "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CronTask":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class CronScheduler:
    """Background cron scheduler with fire queue."""

    def __init__(self, check_interval: int = 60):
        self.check_interval = check_interval
        self.tasks: dict[str, CronTask] = {}
        self._fire_queue: list[CronTask] = []
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self.load()

    def load(self):
        if CRON_FILE.exists():
            try:
                data = json.loads(CRON_FILE.read_text(encoding="utf-8"))
                for d in data.get("tasks", []):
                    t = CronTask.from_dict(d)
                    self.tasks[t.name] = t
            except Exception:
                pass

    def save(self):
        CRON_DIR.mkdir(parents=True, exist_ok=True)
        data = {"tasks": [t.to_dict() for t in self.tasks.values()]}
        CRON_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def add(self, name: str, schedule_text: str, prompt: str) -> tuple[bool, str]:
        """Add a cron task from natural language schedule."""
        name = _regex.sub(r"[^a-z0-9\u4e00-\u9fff_-]", "-", name.lower())[:64]
        interval, desc = parse_schedule(schedule_text)
        if interval <= 0:
            return False, desc
        
        task = CronTask(
            name=name,
            schedule_text=f"{schedule_text} ({desc})",
            prompt=prompt,
            interval_seconds=interval,
            next_run=time.time() + interval,
        )
        with self._lock:
            self.tasks[name] = task
        self.save()
        return True, f"Cron task '{name}' created. Will fire in {desc}."

    def remove(self, name: str) -> bool:
        with self._lock:
            if name in self.tasks:
                del self.tasks[name]
                self.save()
                return True
        return False

    def toggle(self, name: str) -> bool:
        with self._lock:
            if name in self.tasks:
                self.tasks[name].enabled = not self.tasks[name].enabled
                self.save()
                return self.tasks[name].enabled
        return False

    def list_tasks(self) -> list[dict]:
        return [t.to_dict() for t in self.tasks.values()]

    def start(self):
        """Start background ticker thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._ticker, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _ticker(self):
        """Background ticker: check every N seconds."""
        while self._running:
            now = time.time()
            with self._lock:
                for task in list(self.tasks.values()):
                    if task.enabled and now >= task.next_run:
                        task.last_run = now
                        task.next_run = now + task.interval_seconds
                        task.run_count += 1
                        self._fire_queue.append(task)
                if self._fire_queue:
                    self.save()
            time.sleep(self.check_interval)

    def pop_fires(self) -> list[CronTask]:
        """Get and clear the fire queue (called by agent loop)."""
        with self._lock:
            fires = list(self._fire_queue)
            self._fire_queue.clear()
        return fires

    def stats(self) -> dict:
        return {
            "tasks": len(self.tasks),
            "active": sum(1 for t in self.tasks.values() if t.enabled),
            "total_runs": sum(t.run_count for t in self.tasks.values()),
            "running": self._running,
        }


# ── Singleton ──

_cron: CronScheduler | None = None


def get_cron() -> CronScheduler:
    global _cron
    if _cron is None:
        _cron = CronScheduler()
        _cron.start()
    return _cron
