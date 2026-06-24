"""Context Collapse - Smart conversation compression for long sessions.

When conversations exceed token budgets, intelligently collapse
older turns while preserving essential context.

Strategies:
  micro_compact: merge tool results into summaries
  snip_compact: remove intermediate turns
  memory_compact: extract key facts before collapsing
"""
from __future__ import annotations
import re, time
from dataclasses import dataclass, field


@dataclass
class CollapseConfig:
    max_messages: int = 60
    max_tool_results: int = 20
    summary_turn_threshold: int = 10
    auto_compact: bool = True


class ContextCollapser:
    def __init__(self, config: CollapseConfig = None):
        self.config = config or CollapseConfig()
        self._collapse_count = 0

    def should_collapse(self, messages: list[dict]) -> bool:
        if not self.config.auto_compact:
            return False
        if len(messages) > self.config.max_messages:
            return True
        tool_results = sum(1 for m in messages if m.get("role") == "tool")
        return tool_results > self.config.max_tool_results

    def collapse(self, messages: list[dict],
                 keep_last: int = 10) -> tuple[list[dict], str]:
        if len(messages) <= keep_last + self.config.summary_turn_threshold:
            return messages, ""

        split_idx = max(0, len(messages) - keep_last)
        old_messages = messages[:split_idx]
        recent_messages = messages[split_idx:]
        summary = self._summarize(old_messages)

        summary_text = (
            "[Previous conversation summary]"
            + "\n" + summary + "\n" + "[/summary]"
        )
        summary_msg = {"role": "system", "content": summary_text}
        collapsed = [summary_msg] + recent_messages
        self._collapse_count += 1
        return collapsed, summary

    def _summarize(self, messages: list[dict]) -> str:
        lines = []
        for m in messages:
            content = str(m.get("content", ""))[:200]
            role = m.get("role", "?")
            if role == "user":
                lines.append("User: " + content)
            elif role == "assistant":
                tc = m.get("tool_calls", [])
                names = [t.get("function", {}).get("name", "?") for t in tc]
                lines.append("Assistant: " + content[:100] + " [tools: " + str(names) + "]")
            elif role == "tool":
                lines.append("Tool (" + str(m.get("name", "?")) + "): " + content[:100])
        return "\n".join(lines[-30:])

    def estimate_savings(self, messages: list[dict]) -> dict:
        if len(messages) <= self.config.max_messages:
            return {"can_collapse": False, "estimated_savings": 0}
        split_idx = max(0, len(messages) - 10)
        old_count = len(messages[:split_idx])
        old_chars = sum(len(str(m.get("content", ""))) for m in messages[:split_idx])
        return {
            "can_collapse": True,
            "old_messages": old_count,
            "estimated_token_savings": int(old_chars * 0.5),
        }

    @property
    def collapse_count(self) -> int:
        return self._collapse_count


context_collapser = ContextCollapser()
