# -*- coding: utf-8 -*-
"""AutoDream — 自动记忆消化引擎。

Port of cc-haha's src/services/autoDream/.
Background memory consolidation: Orient → Gather → Consolidate → Prune.
五重门控:
  1. 功能开关 (AURORA_AUTO_DREAM=0 禁用)
  2. 时间门检 (距上次整合超过 min_hours 小时)
  3. 数量门检 (新增 session 数 >= min_sessions)
  4. PID 抢锁 (防止多进程并发)
  5. 扫描节流 (scan_interval 内不重复扫描)

Config:
  AURORA_AUTO_DREAM_MIN_HOURS=24
  AURORA_AUTO_DREAM_MIN_SESSIONS=5
  AURORA_AUTO_DREAM_SCAN_INTERVAL=600
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("aurora.auto_dream")

# ── Configuration ───────────────────────────────────────────────

@dataclass
class AutoDreamConfig:
    min_hours: int = 24           # 距上次整合最少小时数
    min_sessions: int = 5         # 新增 session 最少数量
    scan_interval: int = 600      # session 扫描节流(秒)
    lock_stale_seconds: int = 3600  # 锁过期时间
    enabled: bool = True          # 总开关

    @classmethod
    def from_env(cls) -> "AutoDreamConfig":
        return cls(
            min_hours=int(os.getenv("AURORA_AUTO_DREAM_MIN_HOURS", "24")),
            min_sessions=int(os.getenv("AURORA_AUTO_DREAM_MIN_SESSIONS", "5")),
            scan_interval=int(os.getenv("AURORA_AUTO_DREAM_SCAN_INTERVAL", "600")),
            lock_stale_seconds=int(os.getenv("AURORA_AUTO_DREAM_LOCK_STALE", "3600")),
            enabled=os.getenv("AURORA_AUTO_DREAM", "1") not in ("0", "false", "no"),
        )


# ── Consolidation Lock ──────────────────────────────────────────

class ConsolidationLock:
    """PID-based file lock. mtime = lastConsolidatedAt."""

    LOCK_FILE = ".consolidate_lock"

    def __init__(self, memory_dir: str):
        self._path = os.path.join(memory_dir, self.LOCK_FILE)

    def read_last(self) -> float:
        """Read last consolidated timestamp (mtime). Returns 0 if absent."""
        try:
            return os.path.getmtime(self._path)
        except OSError:
            return 0.0

    def try_acquire(self, stale_seconds: int) -> float | None:
        """Try to acquire lock. Returns prior mtime, or None if blocked."""
        pid = os.getpid()
        mtime = self.read_last()

        # Check if current holder is alive
        if mtime > 0 and (time.time() - mtime) < stale_seconds:
            try:
                with open(self._path) as f:
                    holder_pid = int(f.read().strip())
                if self._is_pid_alive(holder_pid):
                    logger.debug(f"AutoDream lock held by PID {holder_pid} ({holder_pid} alive)")
                    return None
            except (ValueError, FileNotFoundError):
                pass  # Stale/corrupted → reclaim

        # Write PID
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            f.write(str(pid))

        # Verify
        try:
            with open(self._path) as f:
                if int(f.read().strip()) != pid:
                    return None
        except Exception:
            return None

        return mtime

    def rollback(self, prior_mtime: float) -> None:
        """Rewind mtime after failed consolidation."""
        try:
            if prior_mtime <= 0:
                os.unlink(self._path)
                return
            with open(self._path, "w") as f:
                f.write("")
            os.utime(self._path, (prior_mtime, prior_mtime))
        except OSError as e:
            logger.debug(f"AutoDream rollback failed: {e}")

    def record(self) -> None:
        """Stamp lock file after manual /dream."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            f.write(str(os.getpid()))

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


# ── Session Scanner ─────────────────────────────────────────────

def list_sessions_since(memory_dir: str, transcript_dir: str, since_ms: float) -> list[str]:
    """List session IDs with mtime after since_ms."""
    session_ids = []
    since = since_ms / 1000.0
    if not os.path.isdir(transcript_dir):
        return session_ids

    for root, dirs, files in os.walk(transcript_dir):
        for fname in files:
            if fname.endswith(".jsonl") and "rollout-" in fname:
                fpath = os.path.join(root, fname)
                try:
                    if os.path.getmtime(fpath) > since:
                        # Extract session id from filename
                        sid = fname.replace("rollout-", "").replace(".jsonl", "")
                        session_ids.append(sid)
                except OSError:
                    pass
    return session_ids


# ── Consolidation Prompt ────────────────────────────────────────

def build_consolidation_prompt(
    memory_dir: str,
    transcript_dir: str,
    session_ids: list[str],
    entrypoint_file: str = "AGENT_MEMORY.md",
    max_entrypoint_lines: int = 50,
) -> str:
    """Build the four-phase AutoDream consolidation prompt."""

    session_list = "\n".join(f"- {s}" for s in session_ids[:20])
    if len(session_ids) > 20:
        session_list += f"\n- ... and {len(session_ids) - 20} more"

    return f"""# Dream: Memory Consolidation

You are performing a dream — a reflective pass over your memory files.
Synthesize what you've learned recently into durable, well-organized
memories so that future sessions can orient quickly.

Memory directory: `{memory_dir}`
Session transcripts: `{transcript_dir}` (large JSONL files — grep narrowly, don't read whole files)

---

## Phase 1 — Orient

- `ls` the memory directory to see what already exists
- Read `{entrypoint_file}` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates

## Phase 2 — Gather recent signal

Look for new information worth persisting. Sources in rough priority order:

1. **Daily logs** if present — these are the append-only stream
2. **Existing memories that drifted** — facts that contradict something you see in the codebase now
3. **Transcript search** — if you need specific context, grep the JSONL transcripts for narrow terms:
   `grep -rn "<narrow term>" {transcript_dir}/ --include="*.jsonl" | tail -50`

Don't exhaustively read transcripts. Look only for things you already suspect matter.

## Phase 3 — Consolidate

For each thing worth remembering, write or update a memory file at the top level of the memory directory.
Focus on:
- Merging new signal into existing topic files rather than creating near-duplicates
- Converting relative dates to absolute dates
- Deleting contradicted facts

## Phase 4 — Prune and index

Update `{entrypoint_file}` so it stays under {max_entrypoint_lines} lines AND under ~25KB.
It's an **index**, not a dump — each entry should be one line under ~150 characters:
`[Title](file.md) — one-line hook`. Never write memory content directly into it.

- Remove pointers to memories that are now stale, wrong, or superseded
- Add pointers to newly important memories
- Resolve contradictions — if two files disagree, fix the wrong one

---

Return a brief summary of what you consolidated, updated, or pruned.
If nothing changed (memories are already tight), say so.

**Tool constraints:** Bash is restricted to read-only commands (ls, find, grep, cat, stat, wc, head, tail).
Anything that writes, redirects to a file, or modifies state will be denied.

Sessions since last consolidation ({len(session_ids)}):
{session_list}
"""


# ── AutoDream Engine ────────────────────────────────────────────

class AutoDream:
    """Background memory consolidation engine.

    Fires the consolidation prompt as a sub-agent when gates pass.
    """

    def __init__(self, memory_dir: str, transcript_dir: str,
                 config: AutoDreamConfig | None = None,
                 dispatch_fn: Callable[[str], Any] | None = None):
        self._memory_dir = memory_dir
        self._transcript_dir = transcript_dir
        self._config = config or AutoDreamConfig.from_env()
        self._dispatch_fn = dispatch_fn  # Function to dispatch prompt to agent
        self._lock = ConsolidationLock(memory_dir)
        self._last_scan_at = 0.0
        self._running = False
        self._task: asyncio.Task | None = None

    # ── Gate Checks ─────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """Check if AutoDream is enabled (master switch)."""
        return self._config.enabled

    def check_time_gate(self) -> bool:
        """Check if enough time has passed since last consolidation."""
        last_at = self._lock.read_last()
        hours_since = (time.time() - last_at) / 3600.0
        return hours_since >= self._config.min_hours

    def check_scan_throttle(self) -> bool:
        """Check if scan throttle allows a new scan."""
        return (time.time() - self._last_scan_at) >= self._config.scan_interval

    def check_session_gate(self, session_ids: list[str]) -> bool:
        """Check if enough new sessions exist."""
        return len(session_ids) >= self._config.min_sessions

    def all_gates_pass(self) -> tuple[bool, str]:
        """Check all gates. Returns (passed, reason)."""
        if not self.is_enabled():
            return False, "disabled"
        if not self.check_time_gate():
            last_at = self._lock.read_last()
            hours = (time.time() - last_at) / 3600.0
            return False, f"time: {hours:.1f}h < {self._config.min_hours}h"
        if not self.check_scan_throttle():
            return False, "scan throttle"
        return True, ""

    # ── Main Loop ───────────────────────────────────────────────

    async def try_consolidate(self) -> dict:
        """Try to run consolidation. Returns result dict."""
        if not self._config.enabled:
            return {"fired": False, "reason": "disabled"}

        # Time gate
        if not self.check_time_gate():
            return {"fired": False, "reason": "time_gate"}

        # Scan throttle
        if not self.check_scan_throttle():
            return {"fired": False, "reason": "scan_throttle"}
        self._last_scan_at = time.time()

        # Session gate
        last_at = self._lock.read_last()
        session_ids = list_sessions_since(
            self._memory_dir, self._transcript_dir, last_at * 1000.0
        )
        if not self.check_session_gate(session_ids):
            logger.debug(f"AutoDream skip: {len(session_ids)} sessions, need {self._config.min_sessions}")
            return {"fired": False, "reason": "session_gate", "session_count": len(session_ids)}

        # Lock
        prior_mtime = self._lock.try_acquire(self._config.lock_stale_seconds)
        if prior_mtime is None:
            return {"fired": False, "reason": "locked"}

        hours_since = (time.time() - prior_mtime) / 3600.0
        logger.info(f"AutoDream firing: {hours_since:.1f}h since last, {len(session_ids)} sessions")

        try:
            prompt = build_consolidation_prompt(
                self._memory_dir, self._transcript_dir, session_ids
            )

            if self._dispatch_fn:
                # Dispatch to agent (async)
                result = self._dispatch_fn(prompt)
                if asyncio.iscoroutine(result):
                    await result
            else:
                logger.warning("AutoDream: no dispatch_fn set, prompt not delivered")
                self._lock.rollback(prior_mtime)
                return {"fired": False, "reason": "no_dispatch"}

            return {
                "fired": True,
                "hours_since": round(hours_since, 1),
                "session_count": len(session_ids),
            }

        except Exception as e:
            logger.error(f"AutoDream failed: {e}")
            self._lock.rollback(prior_mtime)
            return {"fired": False, "reason": f"error: {e}"}

    async def start_background(self, interval: int = 300) -> None:
        """Start background consolidation checker."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._bg_loop(interval))
        logger.info(f"AutoDream background started (interval={interval}s)")

    async def stop(self) -> None:
        """Stop background loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _bg_loop(self, interval: int) -> None:
        """Background loop: check gates periodically."""
        while self._running:
            try:
                result = await self.try_consolidate()
                if result.get("fired"):
                    logger.info(f"AutoDream consolidated: {result}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"AutoDream bg loop error: {e}")
            await asyncio.sleep(interval)

    async def force_dream(self, extra_context: str = "") -> dict:
        """Manually trigger a dream (bypasses gates, still locks)."""
        prior_mtime = self._lock.try_acquire(self._config.lock_stale_seconds)
        if prior_mtime is None:
            return {"fired": False, "reason": "locked"}

        try:
            session_ids = list_sessions_since(
                self._memory_dir, self._transcript_dir, 0.0
            )
            prompt = build_consolidation_prompt(
                self._memory_dir, self._transcript_dir, session_ids
            )
            if extra_context:
                prompt += f"\n\n## Additional context\n\n{extra_context}"

            if self._dispatch_fn:
                result = self._dispatch_fn(prompt)
                if asyncio.iscoroutine(result):
                    await result

            return {"fired": True, "session_count": len(session_ids)}
        except Exception as e:
            self._lock.rollback(prior_mtime)
            return {"fired": False, "reason": str(e)}


# ── Convenience ─────────────────────────────────────────────────

def create_auto_dream(
    memory_dir: str = ".aurora/memory",
    transcript_dir: str = ".aurora/sessions",
) -> AutoDream:
    """Create AutoDream instance with default paths."""
    return AutoDream(memory_dir, transcript_dir, AutoDreamConfig.from_env())
