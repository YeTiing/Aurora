# 上下文管理器 — LLM摘要压缩 + Token预算 + 自动截断
from __future__ import annotations
import time, json, asyncio
from dataclasses import dataclass, field
from typing import Any, Callable
from .token_counter import TokenCounter, counter as default_counter

COMPACTION_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION.
Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed

Be concise, structured, and focused. Output as structured markdown.
Keep under 500 words."""


class CompactionManager:
    """Smart context compaction: checks limits, summarizes old messages, keeps recent ones."""

    def __init__(self, token_counter: TokenCounter | None = None, llm_client=None):
        self._counter = token_counter or default_counter
        self._llm = llm_client
        self._compaction_count = 0

    def set_llm(self, llm_client) -> None:
        self._llm = llm_client

    def should_compact(self, messages: list[dict], max_tokens: int, threshold: float = 0.85) -> bool:
        count = self.estimate_tokens(messages)
        return count > max_tokens * threshold

    def estimate_tokens(self, messages: list[dict]) -> int:
        return self._counter.count_messages(messages)

    def compact(self, messages: list[dict], target_ratio: float = 0.7, keep_recent: int = 4) -> list[dict]:
        if len(messages) <= keep_recent + 2:
            return messages
        recent = messages[-keep_recent:]
        older = messages[:-keep_recent]
        summary_text = self.create_compaction_summary(older)
        summary_msg = {"role": "system", "content": f"## Context Summary (compaction #{self._compaction_count + 1})\n{summary_text}"}
        self._compaction_count += 1
        return [summary_msg] + recent

    def create_compaction_summary(self, older_messages: list[dict]) -> str:
        lines = ["Earlier conversation summarized:", ""]
        for m in older_messages[-40:]:
            role = m.get("role", "?")
            content = str(m.get("content", ""))[:120]
            if content.strip():
                name = m.get("name", "")
                label = f"{role}/{name}" if name else role
                lines.append(f"[{label}] {content}")
        return "\n".join(lines)

    @property
    def compaction_count(self) -> int:
        return self._compaction_count


class LLMCompactor:
    """LLM-powered compactor — calls the model to summarize old messages"""

    def __init__(self, llm_client=None, token_counter=None):
        self._llm = llm_client
        self._counter = token_counter or default_counter

    def set_llm(self, llm_client):
        self._llm = llm_client

    async def compact(self, messages: list[dict], keep_recent: int = 4) -> tuple[list[dict], int]:
        """Use LLM to summarize old messages, keep recent ones"""
        if not self._llm or len(messages) <= keep_recent + 2:
            old = messages[:-keep_recent]
            self._counter.count_messages(messages)
            summary = f"[Compacted] {len(old)} messages summarized:\n"
            for m in old[-20:]:
                content = str(m.get("content", ""))[:100]
                if content.strip():
                    summary += f"- [{m.get('role','?')}] {content}\n"
            return [{"role": "system", "content": summary}] + messages[-keep_recent:], len(old)

        old = messages[:-keep_recent]
        recent = messages[-keep_recent:]

        compact_input = "Summarize the following conversation into a handoff note:\n\n"
        for m in old[-30:]:
            role = m.get("role", "?")
            content = str(m.get("content", ""))[:500]
            compact_input += f"[{role}] {content}\n"

        try:
            resp = await self._llm.chat(
                [{"role": "system", "content": COMPACTION_PROMPT},
                 {"role": "user", "content": compact_input[:4000]}],
                max_tokens=800, temperature=0.0,
            )
            summary = resp.content if resp else compact_input[:500]
        except Exception:
            summary = f"[Compacted] {len(old)} older messages. Key context preserved."

        summary_msg = {"role": "system", "content": f"## Context Summary\n{summary[:2000]}"}
        return [summary_msg] + recent, len(old)


@dataclass
class ContextManager:
    messages: list[dict] = field(default_factory=list)
    compaction_version: int = 0
    token_counter: TokenCounter = field(default_factory=lambda: default_counter)
    max_tokens: int = 24000
    compact_threshold: float = 0.85
    _compactor: LLMCompactor | None = None
    _compaction_mgr: CompactionManager | None = None

    def set_compactor(self, compactor: LLMCompactor):
        self._compactor = compactor

    def set_max_tokens(self, max_tokens: int, threshold: float = 0.85):
        self.max_tokens = max_tokens
        self.compact_threshold = threshold

    def append(self, message: dict):
        self.messages.append(message)

    def append_many(self, messages: list[dict]):
        self.messages.extend(messages)

    @property
    def token_count(self) -> int:
        return self.token_counter.count_messages(self.messages)

    def needs_compaction(self) -> bool:
        if self._compaction_mgr is None:
            self._compaction_mgr = CompactionManager(self.token_counter)
        return self._compaction_mgr.should_compact(self.messages, self.max_tokens, self.compact_threshold)

    def compact(self, summary_llm=None) -> int:
        """Sync compaction (for non-async contexts)"""
        if len(self.messages) < 6:
            return 0
        cm = CompactionManager(self.token_counter)
        if summary_llm:
            cm.set_llm(summary_llm)
        new_msgs = cm.compact(self.messages, keep_recent=4)
        removed = len(self.messages) - len(new_msgs) + 1
        self.messages = new_msgs
        self.compaction_version += 1
        return removed

    async def compact_async(self, llm_client=None) -> int:
        """Async LLM-powered compaction"""
        if len(self.messages) < 6:
            return 0
        if self._compactor is None:
            self._compactor = LLMCompactor(llm_client, self.token_counter)
        if llm_client and not self._compactor._llm:
            self._compactor.set_llm(llm_client)
        if not self._compactor._llm:
            return self.compact()

        new_msgs, count = await self._compactor.compact(self.messages)
        self.messages = new_msgs
        self.compaction_version += 1
        return count

    def truncated_snapshot(self, max_tokens: int) -> list[dict]:
        result = []
        current = 0
        for m in reversed(self.messages):
            tok = self.token_counter.count(json.dumps(m, ensure_ascii=False))
            if current + tok > max_tokens:
                break
            result.insert(0, m)
            current += tok
        return result

    def inject_system(self, content: str):
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = content + "\n\n" + self.messages[0].get("content", "")
        else:
            self.messages.insert(0, {"role": "system", "content": content})

    def clear(self):
        self.messages.clear()
        self.compaction_version = 0

    def stats(self) -> dict:
        return {
            "message_count": len(self.messages),
            "token_count": self.token_count,
            "max_tokens": self.max_tokens,
            "threshold": self.compact_threshold,
            "needs_compaction": self.needs_compaction(),
            "usage_pct": round(self.token_count / max(self.max_tokens, 1) * 100, 1),
            "compaction_version": self.compaction_version,
        }