# Token 预算管理
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class TokenBudget:
    total: int = 24000
    system_prompt: int = 1500
    tool_specs: int = 2000
    rag_context: int = 8000
    conversation_history: int = 8000
    output_reserve: int = 4000

    @property
    def effective_max(self) -> int:
        return self.total - self.output_reserve

    def available(self, used: dict[str, int]) -> dict:
        return {
            "system": self.system_prompt - used.get("system", 0),
            "tools": self.tool_specs - used.get("tools", 0),
            "rag": self.rag_context - used.get("rag", 0),
            "conversation": self.conversation_history - used.get("conversation", 0),
            "output": self.output_reserve,
        }

    def is_over_budget(self, used: dict[str, int]) -> bool:
        return sum(used.values()) > self.total

    def resize_for_model(self, model: str):
        model_limits = {
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "gpt-4-turbo": 128000,
            "gpt-4": 8192,
            "gpt-3.5-turbo": 16384,
            "claude-3-opus": 200000,
            "claude-3.5-sonnet": 200000,
        }
        limit = model_limits.get(model, 24000)
        ratio = limit / 128000
        self.total = min(int(24000 * ratio), limit)
        self.system_prompt = int(1500 * ratio)
        self.tool_specs = int(2000 * ratio)
        self.rag_context = int(8000 * ratio)
        self.conversation_history = int(8000 * ratio)
        self.output_reserve = int(4000 * ratio)