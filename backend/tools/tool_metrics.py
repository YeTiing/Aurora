# -*- coding: utf-8 -*-
"""Tool Metrics — per-tool call tracking, latency, success rate.

Port of cc-haha's tool telemetry.
Records every tool invocation: count, success/fail, latency percentiles, error patterns.
Used to optimize agent behavior and identify slow/failing tools.
"""

from __future__ import annotations
import time, json, threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class ToolCall:
    tool_name: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    success: bool = False
    error: str = ""
    arguments_preview: str = ""
    output_size: int = 0

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000


@dataclass
class ToolStats:
    name: str = ""
    calls: int = 0
    success: int = 0
    failure: int = 0
    total_duration_ms: float = 0.0
    latencies: list[float] = field(default_factory=list)
    last_errors: list[str] = field(default_factory=list)
    last_call_at: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.success / max(self.calls, 1)) * 100

    @property
    def avg_latency_ms(self) -> float:
        return self.total_duration_ms / max(self.calls, 1)

    @property
    def p50_ms(self) -> float:
        return self._percentile(50)

    @property
    def p95_ms(self) -> float:
        return self._percentile(95)

    @property
    def p99_ms(self) -> float:
        return self._percentile(99)

    def _percentile(self, p: float) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * p / 100.0)
        idx = min(idx, len(sorted_lat) - 1)
        return sorted_lat[idx]


class ToolMetrics:
    """Global tool metrics collector."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tools: dict[str, ToolStats] = defaultdict(ToolStats)
        self._recent: list[ToolCall] = []
        self._max_recent = 100
        self._session_start = time.time()

    def record_start(self, tool_name: str, arguments: dict = None) -> str:
        """Record tool invocation start. Returns call_id."""
        call_id = f"{tool_name}_{int(time.time()*1000)}"
        call = ToolCall(
            tool_name=tool_name,
            start_time=time.time(),
            arguments_preview=str(arguments)[:200] if arguments else "",
        )
        with self._lock:
            self._recent.append(call)
            if len(self._recent) > self._max_recent:
                self._recent = self._recent[-self._max_recent:]
        return call_id

    def record_end(self, tool_name: str, success: bool, error: str = "", output_size: int = 0) -> None:
        """Record tool invocation result."""
        now = time.time()
        with self._lock:
            stats = self._tools[tool_name]
            stats.name = tool_name
            stats.calls += 1
            stats.last_call_at = now

            if success:
                stats.success += 1
            else:
                stats.failure += 1
                if error:
                    stats.last_errors.append(error[:200])
                    if len(stats.last_errors) > 20:
                        stats.last_errors = stats.last_errors[-20:]

            # Backfill the most recent call for this tool
            for call in reversed(self._recent):
                if call.tool_name == tool_name and call.end_time == 0.0:
                    call.end_time = now
                    call.success = success
                    call.error = error[:200]
                    call.output_size = output_size
                    duration = call.duration_ms
                    stats.total_duration_ms += duration
                    stats.latencies.append(duration)
                    if len(stats.latencies) > 1000:
                        stats.latencies = stats.latencies[-1000:]
                    break

    def get_stats(self, tool_name: str = "") -> dict:
        """Get stats for a specific tool or all tools."""
        with self._lock:
            if tool_name:
                s = self._tools.get(tool_name)
                return self._stat_to_dict(tool_name, s) if s else {}
            result = {}
            for name, stats in sorted(self._tools.items()):
                result[name] = self._stat_to_dict(name, stats)
            return result

    def get_summary(self) -> dict:
        """Get high-level summary."""
        with self._lock:
            total_calls = sum(s.calls for s in self._tools.values())
            total_success = sum(s.success for s in self._tools.values())
            total_failure = sum(s.failure for s in self._tools.values())
            slowest = sorted(self._tools.items(), key=lambda x: x[1].avg_latency_ms, reverse=True)[:5]
            most_failing = sorted(self._tools.items(), key=lambda x: x[1].failure, reverse=True)[:5]
            return {
                "uptime_sec": round(time.time() - self._session_start, 0),
                "total_calls": total_calls,
                "total_success": total_success,
                "total_failure": total_failure,
                "success_rate": round(total_success / max(total_calls, 1) * 100, 1),
                "unique_tools": len(self._tools),
                "slowest_tools": [(n, round(s.avg_latency_ms, 1)) for n, s in slowest],
                "most_failing": [(n, s.failure) for n, s in most_failing if s.failure > 0],
            }

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Get recent tool calls."""
        with self._lock:
            recent = self._recent[-limit:]
            return [
                {
                    "tool": c.tool_name,
                    "duration_ms": round(c.duration_ms, 1),
                    "success": c.success,
                    "error": c.error[:100] if c.error else "",
                    "args_preview": c.arguments_preview[:100],
                }
                for c in recent
            ]

    def _stat_to_dict(self, name: str, s: ToolStats) -> dict:
        return {
            "name": name,
            "calls": s.calls,
            "success": s.success,
            "failure": s.failure,
            "success_rate": round(s.success_rate, 1),
            "avg_ms": round(s.avg_latency_ms, 1),
            "p50_ms": round(s.p50_ms, 1),
            "p95_ms": round(s.p95_ms, 1),
            "p99_ms": round(s.p99_ms, 1),
            "last_call": s.last_call_at,
            "recent_errors": s.last_errors[-5:],
        }

    def reset(self) -> None:
        with self._lock:
            self._tools.clear()
            self._recent.clear()
            self._session_start = time.time()


_metrics: Optional[ToolMetrics] = None

def get_metrics() -> ToolMetrics:
    global _metrics
    if _metrics is None:
        _metrics = ToolMetrics()
    return _metrics
