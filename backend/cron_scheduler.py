"""
Aurora Cron — Natural-language scheduled tasks with background ticker.

Tasks stored in .aurora/cron.json. Background thread checks every 60s.
When a task fires, it gets pushed into a queue for the agent to consume.

Supports human-language schedules and RRULE (RFC 5545) format.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

CRON_DIR = Path(os.environ.get("AURORA_HOME", ".aurora"))
CRON_FILE = CRON_DIR / "cron.json"

import re as _regex


def _parse_rrule_components(rrule_str: str) -> dict[str, str]:
    """Parse an RRULE string into a dict of components."""
    result = {}
    for part in rrule_str.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        result[k.upper().strip()] = v.strip()
    return result


_DAY_MAP = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def parse_rrule(rrule_str: str) -> tuple[int, str]:
    """Parse an RRULE string (RFC 5545 style) and return (interval_seconds, description).

    Supports: FREQ=DAILY|WEEKLY|HOURLY|MINUTELY|SECONDLY|MONTHLY
              INTERVAL, BYHOUR, BYMINUTE, BYDAY, BYMONTHDAY

    Returns (0, error_msg) on failure.
    """
    comps = _parse_rrule_components(rrule_str)
    freq = comps.get("FREQ", "").upper()
    if not freq:
        return 0, f"Missing FREQ in RRULE: {rrule_str}"

    interval = int(comps.get("INTERVAL", "1"))
    byhour = comps.get("BYHOUR")
    byminute = comps.get("BYMINUTE", "0")
    byday_raw = comps.get("BYDAY")

    now = time.localtime()
    now_epoch = time.time()

    target_hour = int(byhour) if byhour is not None else 0
    target_min = int(byminute)
    target_sec = 0

    if freq == "SECONDLY":
        return interval, f"Every {interval}s"
    elif freq == "MINUTELY":
        return interval * 60, f"Every {interval}min"
    elif freq == "HOURLY":
        bs = interval * 3600
        if byhour is not None or byminute != "0":
            target_of_day = target_hour * 3600 + target_min * 60
            now_of_day = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
            if target_of_day > now_of_day:
                bs = target_of_day - now_of_day
            else:
                bs = 86400 - (now_of_day - target_of_day)
            return bs, f"Daily at {target_hour:02d}:{target_min:02d}"
        return bs, f"Every {interval}h"
    elif freq == "DAILY":
        target_of_day = target_hour * 3600 + target_min * 60
        now_of_day = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
        if target_of_day > now_of_day:
            delay = target_of_day - now_of_day
        else:
            delay = 86400 - (now_of_day - target_of_day)
        delay *= interval
        if interval == 1:
            return delay, f"Daily at {target_hour:02d}:{target_min:02d}"
        return delay, f"Every {interval} days at {target_hour:02d}:{target_min:02d}"
    elif freq == "WEEKLY":
        target_of_day = target_hour * 3600 + target_min * 60
        now_of_day = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
        if byday_raw:
            target_days = []
            for d in byday_raw.split(","):
                d = d.strip().upper()
                if d in _DAY_MAP:
                    target_days.append(_DAY_MAP[d])
        else:
            target_days = [now.tm_wday]

        if not target_days:
            return 0, f"Invalid BYDAY in RRULE: {byday_raw}"

        target_days.sort()
        current_wday = now.tm_wday
        found = False
        for td in target_days:
            if td > current_wday or (td == current_wday and target_of_day > now_of_day):
                days_ahead = td - current_wday
                found = True
                break
        if not found:
            days_ahead = 7 - current_wday + target_days[0]

        delay = days_ahead * 86400 + (target_of_day - now_of_day)
        day_names = [k for k, v in _DAY_MAP.items() if v in target_days]
        return max(delay, 1), f"Weekly on {','.join(day_names)} at {target_hour:02d}:{target_min:02d}"
    elif freq == "MONTHLY":
        target_of_day = target_hour * 3600 + target_min * 60
        now_of_day = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
        target_mday = int(comps.get("BYMONTHDAY", str(now.tm_mday)))
        if target_mday > now.tm_mday or (target_mday == now.tm_mday and target_of_day > now_of_day):
            days_ahead = target_mday - now.tm_mday
        else:
            import calendar
            _, last_day = calendar.monthrange(now.tm_year, now.tm_mon)
            days_ahead = last_day - now.tm_mday + target_mday
        delay = days_ahead * 86400 + (target_of_day - now_of_day)
        return max(delay, 1), f"Monthly on day {target_mday} at {target_hour:02d}:{target_min:02d}"

    return 0, f"Unsupported FREQ in RRULE: {freq}"


def parse_schedule(text: str) -> tuple[int, str]:
    """Parse human schedule text → (interval_seconds, description).
    Tries RRULE parsing first, then falls back to human text patterns.

    Returns (0, error_msg) on failure.
    """
    text = text.lower().strip()

    # Try RRULE first (case-insensitive check for FREQ=)
    if text.upper().startswith("FREQ="):
        return parse_rrule(text)

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
    rrule: Optional[str] = None
    model: str = ""
    reasoning_effort: str = ""
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
            "rrule": self.rrule,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CronTask":
        valid_fields = set(CronTask.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in d.items() if k in valid_fields})


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
        with self._lock:
            data = {"tasks": [t.to_dict() for t in self.tasks.values()]}
        CRON_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def add(self, name: str, schedule_text: str, prompt: str,
            rrule: Optional[str] = None, model: str = "", reasoning_effort: str = "") -> tuple[bool, str]:
        """Add a cron task from natural language schedule or RRULE."""
        name = _regex.sub(r"[^a-z0-9\u4e00-\u9fff_-]", "-", name.lower())[:64]

        interval = 0
        desc = ""
        if rrule:
            interval, desc = parse_rrule(rrule)
        if interval <= 0 and schedule_text:
            interval, desc = parse_schedule(schedule_text)
        if interval <= 0:
            return False, desc

        task = CronTask(
            name=name,
            schedule_text=f"{schedule_text} ({desc})" if schedule_text else desc,
            prompt=prompt,
            interval_seconds=interval,
            next_run=time.time() + interval,
            rrule=rrule or "",
            model=model,
            reasoning_effort=reasoning_effort,
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
                        if task.interval_seconds > 0:
                            task.next_run = now + task.interval_seconds
                        else:
                            task.next_run = now + 3600  # default fallback: 1 hour
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


# ─── Singleton ───

_cron: CronScheduler | None = None


def get_cron() -> CronScheduler:
    global _cron
    if _cron is None:
        _cron = CronScheduler()
        _cron.start()
    return _cron
