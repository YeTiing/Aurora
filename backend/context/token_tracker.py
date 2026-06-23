# Token 使用追踪器
from __future__ import annotations
import time
from dataclasses import dataclass, field

class TokenBudget:
    """Runtime token consumption tracker with budget enforcement."""
    def __init__(self, max_tokens: int, warning_pct: float = 0.8):
        self._max_tokens = max_tokens
        self._used = 0
        self._warning_pct = warning_pct

    def consume(self, n: int) -> dict:
        self._used += n
        remaining = self._max_tokens - self._used
        ratio = self._used / max(self._max_tokens, 1)
        exhausted = self._used > self._max_tokens
        warning = ratio >= self._warning_pct and not exhausted
        return {"ok": not exhausted, "remaining": max(remaining, 0), "warning": warning, "exhausted": exhausted}

    def remaining(self) -> int:
        return max(self._max_tokens - self._used, 0)

    def limit(self) -> int:
        return self._max_tokens

    def usage_ratio(self) -> float:
        return self._used / max(self._max_tokens, 1)

    def reset(self) -> None:
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    def __repr__(self) -> str:
        return f"TokenBudget({self._used}/{self._max_tokens}, {self.usage_ratio():.0%})"


@dataclass
class TokenStats:
    total_prompt: int = 0
    total_completion: int = 0
    total_tokens: int = 0
    request_count: int = 0
    compaction_count: int = 0
    saved_by_compaction: int = 0
    start_time: float = field(default_factory=time.time)

class TokenTracker:
    def __init__(self):
        self.stats = TokenStats()
        self._history: list[dict] = []

    def record_request(self, prompt_tokens: int, completion_tokens: int):
        self.stats.total_prompt += prompt_tokens
        self.stats.total_completion += completion_tokens
        self.stats.total_tokens += prompt_tokens + completion_tokens
        self.stats.request_count += 1

    def record_compaction(self, tokens_saved: int):
        self.stats.compaction_count += 1
        self.stats.saved_by_compaction += tokens_saved

    def summary(self) -> dict:
        elapsed = time.time() - self.stats.start_time
        return {
            "total_tokens": self.stats.total_tokens,
            "prompt_tokens": self.stats.total_prompt,
            "completion_tokens": self.stats.total_completion,
            "requests": self.stats.request_count,
            "compactions": self.stats.compaction_count,
            "saved": self.stats.saved_by_compaction,
            "uptime_sec": int(elapsed),
            "avg_tokens_per_request": self.stats.total_tokens // max(self.stats.request_count, 1),
        }

tracker = TokenTracker()

def truncate_output(output: str, max_chars: int = 16384) -> str:
    if len(output) <= max_chars: return output
    half = max_chars // 2
    return output[:half] + f"\n\n[... {len(output) - max_chars} chars truncated ...]\n\n" + output[-half:]