# LLM 客户端 — 统一入口，整合Provider系统 + Token计数 + 高级特性
from __future__ import annotations
import asyncio, json, time, hashlib, re, threading
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Callable

from .llm_providers import (
    BaseProvider, ProviderConfig, ProviderKind, ProviderPool,
    LLMResponse, StreamChunk, ProviderError, RateLimitError,
    create_provider, OpenAIProvider, ClaudeProvider, OllamaProvider,
)


@dataclass
class LLMConfig:
    """用户友好的LLM配置（与Providers解耦）"""
    provider: str = "openai"      # openai | claude | ollama | openrouter | azure
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    api_version: str = ""
    max_tokens: int = 4096
    temperature: float = 0.3
    top_p: float = 1.0
    max_retries: int = 3
    retry_delay: float = 1.0
    timeout: float = 120.0
    extra_headers: dict = field(default_factory=dict)
    extra_body: dict = field(default_factory=dict)
    json_mode: bool = False
    streaming: bool = True
    enable_tool_use: bool = True

    def to_provider_config(self) -> ProviderConfig:
        return ProviderConfig(
            kind=ProviderKind(self.provider),
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            api_version=self.api_version,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            timeout=self.timeout,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            extra_headers=self.extra_headers,
            extra_body=self.extra_body,
        )


class LLMError(Exception):
    """通用LLM错误"""
    pass


# ═══════════════════════════════════════════════════════════════
# Token 计数器
# ═══════════════════════════════════════════════════════════════

class TokenCounter:
    """基于 tiktoken 的Token计数，失败时回退到字符估算"""

    _lock = threading.Lock()
    _encoders: dict[str, Any] = {}

    MODEL_ENCODING_MAP = {
        "gpt-4o": "o200k_base",
        "gpt-4": "cl100k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-4o-mini": "o200k_base",
        "gpt-3.5-turbo": "cl100k_base",
        "claude-": "cl100k_base",  # Claude 也用类似编码估算
    }

    @classmethod
    def _get_encoder(cls, model: str):
        with cls._lock:
            if model in cls._encoders:
                return cls._encoders[model]

        encoding_name = "cl100k_base"
        for prefix, name in cls.MODEL_ENCODING_MAP.items():
            if model.startswith(prefix):
                encoding_name = name
                break

        try:
            import tiktoken
            encoder = tiktoken.get_encoding(encoding_name)
        except (ImportError, Exception):
            encoder = None

        with cls._lock:
            cls._encoders[model] = encoder
        return encoder

    @classmethod
    def count(cls, text: str, model: str = "gpt-4o") -> int:
        if not text:
            return 0
        encoder = cls._get_encoder(model)
        if encoder:
            try:
                return len(encoder.encode(text))
            except Exception:
                pass
        return len(text) // 4  # 粗略估算

    @classmethod
    def count_messages(cls, messages: list[dict], model: str = "gpt-4o") -> int:
        """计算消息列表的Token数（含格式开销）"""
        total = 0
        for msg in messages:
            total += 4  # 消息格式固定开销
            for key, val in msg.items():
                if isinstance(val, str):
                    total += cls.count(val, model)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            total += cls.count(json.dumps(item, ensure_ascii=False), model)
            if msg.get("role") == "tool":
                total -= 2  # tool 消息开销稍低
        total += 2  # 整体 prime
        return total


# ═══════════════════════════════════════════════════════════════
# LLM 客户端 — 统一入口
# ═══════════════════════════════════════════════════════════════

class LLMClient:
    """Aurora 统一LLM客户端 — 支持单Provider / Provider池 / 流式 / 重试"""

    def __init__(
        self,
        config: LLMConfig | None = None,
        fallback_providers: list[LLMConfig] | None = None,
    ):
        self.config = config or LLMConfig()
        self._provider: BaseProvider | None = None
        self._pool: ProviderPool | None = None
        self._use_pool = bool(fallback_providers)

        if self._use_pool:
            providers = [create_provider(**self.config.to_provider_config().__dict__)]
            for fb in fallback_providers or []:
                providers.append(create_provider(**fb.to_provider_config().__dict__))
            self._pool = ProviderPool(providers)
        else:
            self._provider = create_provider(
                kind=self.config.provider,
                model=self.config.model,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                api_version=self.config.api_version,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                top_p=self.config.top_p,
                timeout=self.config.timeout,
                max_retries=self.config.max_retries,
                retry_delay=self.config.retry_delay,
                extra_headers=self.config.extra_headers,
                extra_body=self.config.extra_body,
            )

        self._total_requests = 0
        self._total_tokens = 0
        self._token_counter = TokenCounter()

    @property
    def stats(self) -> dict:
        if self._pool:
            return {"pool": True, "providers": self._pool.stats, "total_requests": self._total_requests, "total_tokens": self._total_tokens}
        p = self._provider
        if p:
            return {**p.stats, "total_requests": self._total_requests, "total_tokens": self._total_tokens}
        return {"total_requests": self._total_requests, "total_tokens": self._total_tokens}

    def count_tokens(self, text_or_messages, model: str | None = None) -> int:
        """计算文本或消息列表的Token数"""
        m = model or self.config.model
        if isinstance(text_or_messages, list):
            return TokenCounter.count_messages(text_or_messages, m)
        return TokenCounter.count(str(text_or_messages), m)

    # ── 非流式 ──

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        **kwargs,
    ) -> LLMResponse:
        """非流式对话"""
        self._total_requests += 1
        opts = {**kwargs}
        if json_mode or self.config.json_mode:
            opts["json_mode"] = True

        if self._pool:
            resp = await self._pool.chat(messages, tools, **opts)
        else:
            resp = await self._provider.chat(messages, tools, **opts)

        self._total_tokens += resp.total_tokens
        return resp

    # ── 流式 ──

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token: Callable[[str], Any] | None = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """流式对话 — 可选回调 on_token(content)"""
        self._total_requests += 1
        total_content = ""

        source = self._pool.chat_stream(messages, tools, **kwargs) if self._pool else self._provider.chat_stream(messages, tools, **kwargs)

        async for chunk in source:
            if chunk.content:
                total_content += chunk.content
                if on_token:
                    on_token(chunk.content)
            if chunk.usage:
                self._total_tokens += chunk.usage.get("total_tokens", 0)
            yield chunk

    # ── 便捷方法 ──

    async def chat_simple(self, user_message: str, system_prompt: str = "", **kwargs) -> str:
        """最简单的单轮对话"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        resp = await self.chat(messages, **kwargs)
        return resp.content

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tool_rounds: int = 10,
        tool_handler: Callable[[str, dict], Any] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """带自动工具调用的对话循环"""
        current_messages = list(messages)
        rounds = 0

        while rounds < max_tool_rounds:
            resp = await self.chat(current_messages, tools, **kwargs)

            if resp.finish_reason == "stop" or not resp.tool_calls:
                return resp

            # 执行工具调用
            current_messages.append({
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                } for tc in resp.tool_calls],
            })

            for tc in resp.tool_calls:
                if tool_handler:
                    try:
                        args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                        result = await tool_handler(tc["name"], args) if asyncio.iscoroutinefunction(tool_handler) else tool_handler(tc["name"], args)
                    except Exception as e:
                        result = json.dumps({"error": str(e)})
                else:
                    result = json.dumps({"error": "No tool handler configured"})

                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result,
                })

            rounds += 1

        return LLMResponse(content="Tool call loop exceeded max rounds", finish_reason="stop")

    # ── 嵌入 ──

    async def embeddings(self, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
        """获取文本嵌入"""
        if self._pool:
            pp = self._pool._next_provider()
            if pp:
                provider = pp.provider
            else:
                raise LLMError("No healthy provider for embeddings")
        else:
            provider = self._provider
        return await provider.embeddings(texts, model)

    # ── 结构化输出 ──

    async def structured_output(
        self,
        messages: list[dict],
        output_schema: dict,
        model: str | None = None,
        **kwargs,
    ) -> dict:
        """强制JSON结构化输出"""
        system_msg = {
            "role": "system",
            "content": f"You must respond with valid JSON conforming to this schema:\n{json.dumps(output_schema, indent=2)}\n\nRespond ONLY with the JSON, no other text.",
        }
        msgs = [system_msg] + messages
        resp = await self.chat(msgs, json_mode=True, model=model or self.config.model, **kwargs)

        content = resp.content.strip()
        if content.startswith("```"):
            content = re.sub(r'^```\w*\n?', '', content)
            content = re.sub(r'\n?```$', '', content)

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            try:
                return json.loads(re.search(r'\{[\s\S]*\}', content).group(0))
            except (json.JSONDecodeError, AttributeError):
                raise LLMError(f"Failed to parse structured output: {content[:200]}")

    async def close(self):
        if self._pool:
            await self._pool.close()
        elif self._provider:
            await self._provider.close()


# ═══════════════════════════════════════════════════════════════
# Mock — 用于测试
# ═══════════════════════════════════════════════════════════════

class MockLLMClient(LLMClient):
    """无网络时的Mock客户端 — 返回预设回复"""

    def __init__(self):
        super().__init__(LLMConfig(provider="openai", model="mock", api_key="mock-key"))

    def _generate_mock_response(self, user_input: str, tools: list | None) -> str:
        inp = user_input.lower()
        if not inp:
            return "I'm here to help. What would you like me to do?"
        if "hello" in inp or "你好" in inp:
            return "Hello! I'm Aurora, your AI coding agent. What shall we build today?"
        if "plan" in inp or "计划" in inp:
            return json.dumps([
                {"step": 1, "action": "需求分析与技术选型", "details": "确认功能边界与实现方案"},
                {"step": 2, "action": "核心功能实现", "details": "实现主要逻辑与接口"},
                {"step": 3, "action": "测试与验证", "details": "单元测试+集成测试+边界case"},
                {"step": 4, "action": "代码审查与优化", "details": "审查代码质量与性能"},
            ], ensure_ascii=False)
        if "帮我" in inp or "write" in inp or "写" in inp or "build" in inp:
            return "我来帮你实现这个。先看一下代码结构，再生成方案。需要我调用工具搜索文件的话，在下一步发起。"
        if "fix" in inp or "修复" in inp or "bug" in inp:
            return "我来诊断这个问题。先搜索相关代码，定位根因，再给出修复方案。"
        if "test" in inp or "测试" in inp:
            return "我来写测试用例。先看看现有测试的风格，然后生成覆盖主要路径和边界的测试。"
        return f"收到了 '{user_input[:50]}...'。你想让我怎么处理这个？具体说说需求。"

    async def chat(self, messages, tools=None, **kwargs):
        self._total_requests += 1
        last_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_msg = m.get("content", "")
                break
        response = self._generate_mock_response(last_msg, tools)
        return LLMResponse(
            content=response,
            finish_reason="stop",
            model="mock",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )

    async def chat_stream(self, messages, tools=None, on_token=None, **kwargs):
        last_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_msg = m.get("content", "")
                break
        resp = self._generate_mock_response(last_msg, tools)
        for i in range(0, len(resp), 8):
            chunk = resp[i:i+8]
            if on_token:
                on_token(chunk)
            yield StreamChunk(content=chunk)
            await asyncio.sleep(0.01)

    async def chat_simple(self, user_message, system_prompt="", **kwargs):
        return self._generate_mock_response(user_message, None)

    async def embeddings(self, texts, model="text-embedding-3-small"):
        import random
        random.seed(42)
        return [[random.random() for _ in range(768)] for _ in texts]

    async def close(self):
        pass


# ═══════════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════════

def create_llm_client(
    use_mock: bool = False,
    fallback_configs: list[LLMConfig] | None = None,
    **config_overrides,
) -> LLMClient:
    """便捷工厂 — 从环境变量 + 配置创建客户端"""
    if use_mock:
        return MockLLMClient()

    import os
    cfg = LLMConfig(
        provider=config_overrides.pop("provider", os.environ.get("AURORA_LLM_PROVIDER", "openai")),
        model=config_overrides.pop("model", os.environ.get("AURORA_LLM_MODEL", "gpt-4o")),
        api_key=config_overrides.pop("api_key", os.environ.get("AURORA_API_KEY", os.environ.get("OPENAI_API_KEY", ""))),
        base_url=config_overrides.pop("base_url", os.environ.get("AURORA_BASE_URL", "")),
        **config_overrides,
    )

    fb_configs = None
    if fallback_configs:
        fb_configs = fallback_configs

    return LLMClient(cfg, fallback_providers=fb_configs)