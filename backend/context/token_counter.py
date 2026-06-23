# Token 计数器 — 多模型支持
from __future__ import annotations
import tiktoken

MODEL_ENCODINGS = {
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "claude-3-opus": "cl100k_base",
    "claude-3.5-sonnet": "cl100k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-ada-002": "cl100k_base",
}

MESSAGE_OVERHEAD = 4
REPLY_OVERHEAD = 2

class TokenCounter:
    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        encoding_name = MODEL_ENCODINGS.get(model, "cl100k_base")
        try:
            self.encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            self.encoder = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        return len(self.encoder.encode(text))

    def count_messages(self, messages: list[dict]) -> int:
        total = 0
        for msg in messages:
            total += MESSAGE_OVERHEAD
            for key, value in msg.items():
                if isinstance(value, str):
                    total += self.count(value)
                elif isinstance(value, list):
                    total += self.count(str(value))
                elif key == "name":
                    total += self.count(str(value)) - 1  # name already accounted
        total += REPLY_OVERHEAD
        return total

    def count_tool_schemas(self, tools: list[dict]) -> int:
        import json
        return self.count(json.dumps(tools, ensure_ascii=False))

    def change_model(self, model: str):
        if model != self.model:
            self.__init__(model)


counter = TokenCounter()