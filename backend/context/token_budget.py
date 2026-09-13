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
