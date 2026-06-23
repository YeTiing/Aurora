# coding: utf-8
"""Observability stats aggregator for the monitoring dashboard."""
from __future__ import annotations
import time, threading
from typing import Any

_llm_latencies: list[float] = []
_llm_latencies_lock = threading.Lock()
_llm_latencies_max = 200

_turn_durations: list[dict] = []
_turn_durations_lock = threading.Lock()
_turn_durations_max = 50

_tool_call_counts: dict[str, int] = {}
_tool_call_counts_lock = threading.Lock()

_error_count: int = 0
_total_turns: int = 0
_stats_lock = threading.Lock()

_rate_limit_count: int = 0
_rate_limit_delay: float = 0
_rate_limit_lock = threading.Lock()


def record_llm_latency(ms: float):
    with _llm_latencies_lock:
        _llm_latencies.append(ms)
        if len(_llm_latencies) > _llm_latencies_max:
            _llm_latencies[:] = _llm_latencies[-_llm_latencies_max:]

def record_turn(turn_duration_ms: float, error: bool = False):
    with _turn_durations_lock:
        _turn_durations.append({"ts": time.time(), "duration_ms": turn_duration_ms, "error": error})
        if len(_turn_durations) > _turn_durations_max:
            _turn_durations[:] = _turn_durations[-_turn_durations_max:]
    with _stats_lock:
        global _total_turns, _error_count
        _total_turns += 1
        if error:
            _error_count += 1

def record_tool_call(tool_name: str):
    with _tool_call_counts_lock:
        _tool_call_counts[tool_name] = _tool_call_counts.get(tool_name, 0) + 1

def record_rate_limit(delay: float):
    with _rate_limit_lock:
        global _rate_limit_count, _rate_limit_delay
        _rate_limit_count += 1
        _rate_limit_delay = delay


def get_stats() -> dict[str, Any]:
    """Aggregate all observability stats for the dashboard."""
    from backend.observability.tracer import metrics as m

    msnap = m.snapshot()

    with _llm_latencies_lock:
        lats = list(_llm_latencies)
    latencies = lats[-20:] if len(lats) > 20 else lats

    with _stats_lock:
        total_turns = _total_turns
        error_count = _error_count
    error_rate = (error_count / total_turns * 100) if total_turns > 0 else 0

    with _tool_call_counts_lock:
        tool_calls = dict(_tool_call_counts)

    with _turn_durations_lock:
        turns = list(_turn_durations)
    recent_turns = turns[-20:]
    avg_duration = sum(t["duration_ms"] for t in turns) / len(turns) if turns else 0

    with _rate_limit_lock:
        rl_count = _rate_limit_count
        rl_delay = _rate_limit_delay

    tokens_counter = msnap.get("counters", {}).get("llm_tokens_total", {})
    tokens_used = tokens_counter.get("value", 0)

    llm_lat_hist = msnap.get("histograms", {}).get("llm_latency_ms", {})

    return {
        "tokens": {
            "used": tokens_used,
            "limit": 128000,
            "pct": round(tokens_used / 128000 * 100, 1) if tokens_used else 0,
        },
        "latency": {
            "avg": llm_lat_hist.get("avg", 0),
            "p50": llm_lat_hist.get("p50", 0),
            "p95": llm_lat_hist.get("p95", 0),
            "max": max(*lats) if lats else 0,
            "recent": latencies,
        },
        "errors": {
            "count": error_count,
            "rate": round(error_rate, 1),
        },
        "tool_calls": tool_calls,
        "turns": {
            "total": total_turns,
            "avg_duration": round(avg_duration, 1),
            "recent": recent_turns,
        },
        "rate_limits": {
            "count": rl_count,
            "current_delay": rl_delay,
        },
    }