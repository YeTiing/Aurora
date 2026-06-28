# LLM Providers — OpenAI / Claude / 本地模型统一抽象
"""多Provider LLM后端：OpenAI、Anthropic Claude、Ollama本地模型、OpenRouter代理"""
from __future__ import annotations
import asyncio, json, time, hashlib, os, re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional
from backend.agent.tool_call_aggregator import tool_call_aggregator
from enum import Enum


class ProviderKind(Enum):
    OPENAI = "openai"
    CLAUDE = "claude"
    OLLAMA = "ollama"
    OPENROUTER = "openrouter"
    AZURE = "azure"
    DEEPSEEK = "deepseek"
    CUSTOM = "custom"


@dataclass
class ProviderConfig:
    kind: ProviderKind = ProviderKind.OPENAI
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    api_version: str = ""       # Azure 专用
    max_tokens: int = 4096
    temperature: float = 0.3
    top_p: float = 1.0
    timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 1.0
    extra_headers: dict = field(default_factory=dict)
    extra_body: dict = field(default_factory=dict)
    wire_api: str = "chat"  # "chat" | "responses" - matches Codex wire_api

    @property
    def endpoint(self) -> str:
        if self.kind == ProviderKind.OPENAI:
            base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
            return base + ("/responses" if self.wire_api == "responses" else "/chat/completions")
        elif self.kind == ProviderKind.CLAUDE:
            return (self.base_url or "https://api.anthropic.com/v1").rstrip("/") + "/messages"
        elif self.kind == ProviderKind.OLLAMA:
            return (self.base_url or "http://localhost:11434").rstrip("/") + "/api/chat"
        elif self.kind == ProviderKind.OPENROUTER:
            return "https://openrouter.ai/api/v1/chat/completions"
        elif self.kind == ProviderKind.DEEPSEEK:
            return (self.base_url or "https://api.deepseek.com").rstrip("/") + "/chat/completions"
        elif self.kind == ProviderKind.AZURE:
            base = self.base_url.rstrip("/") if self.base_url else f"https://{os.environ.get('AZURE_RESOURCE_NAME','')}.openai.azure.com"
            return f"{base}/openai/deployments/{self.model}/chat/completions?api-version={self.api_version or '2024-02-15-preview'}"
        return self.base_url


@dataclass
class LLMResponse:
    """统一的LLM响应格式"""
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    model: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)


@dataclass
class StreamChunk:
    """流式响应的单个chunk"""
    content: str = ""
    tool_call_delta: dict | None = None
    finish_reason: str | None = None
    usage: dict | None = None


# ═══════════════════════════════════════════════════════════════
# Provider Error Hierarchy
# ═══════════════════════════════════════════════════════════════

class ProviderError(Exception):
    """Provider 层面错误"""
    def __init__(self, message: str, status_code: int = 0, provider: str = "", retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.retryable = retryable

class RateLimitError(ProviderError):
    def __init__(self, message="Rate limited", retry_after: float = 1.0, **kw):
        super().__init__(message, status_code=429, retryable=True, **kw)
        self.retry_after = retry_after

class AuthError(ProviderError):
    def __init__(self, message="Authentication failed", **kw):
        super().__init__(message, status_code=401, retryable=False, **kw)

class ContextOverflowError(ProviderError):
    def __init__(self, message="Context length exceeded", **kw):
        super().__init__(message, status_code=400, retryable=False, **kw)

class ServerOverloadError(ProviderError):
    def __init__(self, message="Server overloaded", retry_after: float = 5.0, **kw):
        super().__init__(message, status_code=529, retryable=True, **kw)
        self.retry_after = retry_after


# ═══════════════════════════════════════════════════════════════
# Base Provider
# ═══════════════════════════════════════════════════════════════

class BaseProvider(ABC):
    """所有LLM Provider的基类"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._session = None
        self._total_requests = 0
        self._total_tokens = 0
        self._total_latency = 0.0

    @property
    def kind(self) -> ProviderKind:
        return self.config.kind

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def stats(self) -> dict:
        return {
            "provider": self.config.kind.value,
            "model": self.config.model,
            "requests": self._total_requests,
            "total_tokens": self._total_tokens,
            "avg_latency_ms": self._total_latency / max(self._total_requests, 1),
        }

    async def _get_session(self):
        if self._session is None:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            headers = self._build_headers()
            self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        return self._session

    @abstractmethod
    def _build_headers(self) -> dict:
        ...

    @abstractmethod
    def _build_payload(self, messages: list[dict], tools: list[dict] | None, **kwargs) -> dict:
        ...

    @abstractmethod
    def _parse_response(self, data: dict) -> LLMResponse:
        ...

    @abstractmethod
    def _parse_stream_chunk(self, data: dict) -> StreamChunk:
        ...

    @abstractmethod
    def _convert_tools(self, tools: list[dict]) -> Any:
        """将通用 tool schema 转换成 provider 格式"""
        ...

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> LLMResponse:
        """非流式对话，带指数退避重试"""
        last_error: ProviderError | None = None
        for attempt in range(self.config.max_retries):
            try:
                t0 = time.perf_counter()
                resp = await self._chat_once(messages, tools, **kwargs)
                resp.latency_ms = (time.perf_counter() - t0) * 1000
                self._total_requests += 1
                self._total_tokens += resp.total_tokens
                self._total_latency += resp.latency_ms
                return resp
            except RateLimitError as e:
                wait = max(self.config.retry_delay * (2 ** attempt), e.retry_after)
                await asyncio.sleep(wait)
                last_error = e
            except ServerOverloadError as e:
                wait = max(self.config.retry_delay * (2 ** attempt), e.retry_after)
                await asyncio.sleep(wait)
                last_error = e
            except (AuthError, ContextOverflowError):
                raise  # 不可重试的直接抛
            except ProviderError as e:
                if attempt < self.config.max_retries - 1 and e.retryable:
                    await asyncio.sleep(self.config.retry_delay * (2 ** attempt))
                last_error = e
        raise last_error or ProviderError("Max retries exceeded", provider=self.config.kind.value)

    @abstractmethod
    async def _chat_once(self, messages: list[dict], tools: list[dict] | None, **kwargs) -> LLMResponse:
        ...

    @abstractmethod
    async def chat_stream(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> AsyncIterator[StreamChunk]:
        ...

    async def embeddings(self, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
        """获取嵌入向量（仅 OpenAI / Ollama 支持）"""
        raise NotImplementedError(f"{self.config.kind.value} does not support embeddings")

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    def _classify_error(self, status: int, body: str) -> ProviderError:
        """根据HTTP状态码和响应体分类错误"""
        if status == 429:
            return RateLimitError(provider=self.config.kind.value)
        if status == 401 or status == 403:
            return AuthError(provider=self.config.kind.value)
        if status == 529 or status == 503:
            return ServerOverloadError(provider=self.config.kind.value)
        if "context_length" in body.lower() or "maximum context" in body.lower() or "token" in body.lower():
            return ContextOverflowError(body[:200], provider=self.config.kind.value)
        return ProviderError(body[:500], status_code=status, provider=self.config.kind.value, retryable=status >= 500)


# ═══════════════════════════════════════════════════════════════
# OpenAI Provider
# ═══════════════════════════════════════════════════════════════

class OpenAIProvider(BaseProvider):
    """OpenAI / 兼容API (DeepSeek, Groq, Fireworks 等)"""

    
    def _build_responses_payload(self, messages: list[dict], tools: list[dict] | None, **kwargs) -> dict:
        """Build Responses API payload (Codex wire format)"""
        # Convert messages to Responses API "input" format
        inp = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, str):
                inp.append({"role": role, "content": content})
            elif isinstance(content, list):
                inp.append({"role": role, "content": content})

        payload = {
            "model": self.config.model,
            "input": inp,
            "temperature": self.config.temperature,
        }
        if tools:
            payload["tools"] = tools
        if self.config.max_tokens:
            payload["max_output_tokens"] = self.config.max_tokens
        # Reasoning effort support
        reasoning = kwargs.get("reasoning_effort")
        if reasoning:
            payload["reasoning"] = {"effort": reasoning}
        return payload

    def _parse_responses_response(self, data: dict) -> LLMResponse:
        """Parse a non-streaming Responses API response into LLMResponse"""
        output = data.get("output", [])
        content_parts = []
        tool_calls = []
        for item in output:
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        content_parts.append(part.get("text", ""))
            elif item.get("type") == "function_call":
                tool_calls.append({
                    "id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "{}"),
                })
        return LLMResponse(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=data.get("status", "completed"),
            model=data.get("model", self.config.model),
            usage=data.get("usage", {}),
            raw=data,
        )

    def _parse_responses_stream(self, event: dict) -> StreamChunk | None:
        """Parse a Responses API SSE event into StreamChunk"""
        etype = event.get("type", "")
        if etype == "response.output_text.delta":
            return StreamChunk(content=event.get("delta", ""))
        elif etype == "response.content_part.added":
            part = event.get("part", {})
            if part.get("type") == "function_call":
                return StreamChunk(tool_call_delta={
                    "index": event.get("content_index", 0),
                    "id": part.get("call_id", ""),
                    "name": part.get("name", ""),
                    "arguments": ""
                })
        elif etype == "response.function_call_arguments.delta":
            return StreamChunk(tool_call_delta={
                "index": event.get("content_index", 0),
                "arguments_delta": event.get("delta", "")
            })
        elif etype == "response.completed":
            usage = event.get("usage", {})
            return StreamChunk(finish_reason="stop", usage=usage)
        elif etype == "error":
            return StreamChunk(finish_reason="error")
        return None

    def _build_headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        h.update(self.config.extra_headers)
        return h

    def _build_payload(self, messages: list[dict], tools: list[dict] | None, **kwargs) -> dict:
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "top_p": kwargs.get("top_p", self.config.top_p),
        }
        if tools:
            payload["tools"] = self._convert_tools(tools)
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")
        if kwargs.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}
        if self.config.extra_body:
            payload.update(self.config.extra_body)
        return payload

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        converted = []
        for t in tools:
            ct: dict = {"type": "function", "function": {
                "name": t.get("name", t.get("function", {}).get("name", "")),
                "description": t.get("description", t.get("function", {}).get("description", "")),
                "parameters": t.get("parameters", t.get("function", {}).get("parameters", {})),
            }}
            if "strict" in t:
                ct["function"]["strict"] = t["strict"]
            converted.append(ct)
        return converted

    def _parse_response(self, data: dict) -> LLMResponse:
        choice = (data.get("choices", [{}]) or [{}])[0]
        message = choice.get("message", {})
        tool_calls_raw = message.get("tool_calls", [])
        tool_calls = [{
            "id": tc.get("id", ""),
            "name": tc.get("function", {}).get("name", ""),
            "arguments": tc.get("function", {}).get("arguments", "{}"),
        } for tc in tool_calls_raw]

        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            model=data.get("model", self.config.model),
            usage=data.get("usage", {}),
            raw=data,
        )

    def _parse_stream_chunk(self, data: dict) -> StreamChunk:
        choice = (data.get("choices", [{}]) or [{}])[0]
        delta = choice.get("delta", {})
        tc_delta_raw = (delta.get("tool_calls") or [None])[0]
        tc_delta = None
        if tc_delta_raw:
            tc_delta = {
                "index": tc_delta_raw.get("index", 0),
                "id": tc_delta_raw.get("id"),
                "name": tc_delta_raw.get("function", {}).get("name"),
                "arguments": tc_delta_raw.get("function", {}).get("arguments"),
            }
        return StreamChunk(
            content=delta.get("content") or "",
            tool_call_delta=tc_delta,
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage"),
        )

    async def _chat_once(self, messages, tools, **kwargs) -> LLMResponse:
        session = await self._get_session()
        use_responses = self.config.wire_api == "responses"
        if use_responses:
            payload = self._build_responses_payload(messages, tools, **kwargs)
        else:
            payload = self._build_payload(messages, tools, **kwargs)
        url = self.config.endpoint

        async with session.post(url, json=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise self._classify_error(resp.status, text)
            data = json.loads(text)
            if use_responses:
                return self._parse_responses_response(data)
            return self._parse_response(data)

    async def chat_stream(self, messages, tools=None, **kwargs) -> AsyncIterator[StreamChunk]:
        session = await self._get_session()
        use_responses = self.config.wire_api == "responses"
        if use_responses:
            payload = self._build_responses_payload(messages, tools, **kwargs)
            payload["stream"] = True
        else:
            payload = self._build_payload(messages, tools, **kwargs)
            payload["stream"] = True
        url = self.config.endpoint

        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise self._classify_error(resp.status, text)
            if use_responses:
                # Responses API SSE: parse event-type + data line pairs
                event_type = ""
                async for chunk in resp.content:
                    line = chunk.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                        continue
                    elif line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            event = json.loads(data_str)
                            event["type"] = event_type
                            self._feed_aggregator(event)
                            parsed = self._parse_responses_stream(event)
                            if parsed:
                                yield parsed
                        except json.JSONDecodeError:
                            continue
            else:
                async for line in resp.content:
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str.startswith("data: "):
                        continue
                    data_str = line_str[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        yield self._parse_stream_chunk(json.loads(data_str))
                    except json.JSONDecodeError:
                        continue

    async def embeddings(self, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
        session = await self._get_session()
        url = self.config.base_url.rstrip("/") + "/embeddings" if self.config.base_url else "https://api.openai.com/v1/embeddings"
        payload = {"model": model, "input": texts}
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise self._classify_error(resp.status, text)
            data = await resp.json()
            items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
            return [d.get("embedding", []) for d in items]


# ═══════════════════════════════════════════════════════════════
# Claude (Anthropic) Provider
# ═══════════════════════════════════════════════════════════════


    def _feed_aggregator(self, event: dict) -> None:
        etype = event.get("type", "")
        call_id = event.get("call_id", event.get("content_index", event.get("response_id", "")))
        if isinstance(call_id, int):
            call_id = str(call_id)
        if etype == "response.content_part.added":
            part = event.get("part", {})
            if part.get("type") == "function_call":
                cid = part.get("call_id", "")
                tool_call_aggregator.start_tool_block(cid, tool_name=part.get("name", ""), call_id=cid)
        elif etype == "response.function_call_arguments.delta":
            delta = event.get("delta", "")
            if delta and call_id:
                if call_id not in tool_call_aggregator.active_blocks:
                    name = event.get("name", "") or "unknown"
                    tool_call_aggregator.start_tool_block(call_id, tool_name=name, call_id=call_id)
                tool_call_aggregator.append_json_fragment(call_id, delta)
        elif etype == "response.completed":
            for bid in list(tool_call_aggregator.active_blocks.keys()):
                tool_call_aggregator.finalize_tool_block(bid)


class ClaudeProvider(BaseProvider):
    """Anthropic Claude Messages API"""

    def _build_headers(self) -> dict:
        h = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        h.update(self.config.extra_headers)
        return h

    def _build_payload(self, messages: list[dict], tools: list[dict] | None, **kwargs) -> dict:
        system_msgs = [m["content"] for m in messages if m.get("role") == "system"]
        conv = [m for m in messages if m.get("role") != "system"]

        system_prompt = "\n\n".join(system_msgs) if system_msgs else None

        payload: dict = {
            "model": self.config.model,
            "messages": self._convert_messages(conv),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "top_p": kwargs.get("top_p", self.config.top_p),
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = self._convert_tools(tools)
        if self.config.extra_body:
            payload.update(self.config.extra_body)
        return payload

    def _convert_messages(self, messages: list[dict]) -> list[dict]:
        """OpenAI格式 → Claude格式"""
        converted = []
        for m in messages:
            role = m.get("role", "user")
            if role == "system":
                continue
            if role == "assistant" and m.get("tool_calls"):
                # Claude 格式的 tool_use
                parts = []
                if m.get("content"):
                    parts.append({"type": "text", "text": m["content"]})
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function", tc)
                    parts.append({
                        "type": "tool_use",
                        "id": tc.get("id", f"call_{hashlib.md5(str(tc).encode()).hexdigest()[:8]}"),
                        "name": fn.get("name", ""),
                        "input": json.loads(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else fn.get("arguments", {}),
                    })
                converted.append({"role": "assistant", "content": parts})
            elif role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }],
                })
            else:
                content = m.get("content", "")
                if isinstance(content, str) and content:
                    converted.append({"role": role, "content": content})
        return converted

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        converted = []
        for t in tools:
            fn = t.get("function", t)
            converted.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return converted

    def _parse_response(self, data: dict) -> LLMResponse:
        content_blocks = data.get("content", [])
        text_parts = []
        tool_calls = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                })

        usage = data.get("usage", {})
        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=data.get("stop_reason", "end_turn"),
            model=data.get("model", self.config.model),
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
            raw=data,
        )

    def _parse_stream_chunk(self, data: dict) -> StreamChunk:
        if data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                return StreamChunk(content=delta.get("text", ""))
            elif delta.get("type") == "input_json_delta":
                return StreamChunk(tool_call_delta={"arguments": delta.get("partial_json", "")})
        elif data.get("type") == "message_stop":
            return StreamChunk(finish_reason="stop")
        elif data.get("type") == "message_delta":
            usage = data.get("usage", {})
            if usage:
                return StreamChunk(usage={
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                })
        return StreamChunk()

    async def _chat_once(self, messages, tools, **kwargs) -> LLMResponse:
        session = await self._get_session()
        use_responses = self.config.wire_api == "responses"
        if use_responses:
            payload = self._build_responses_payload(messages, tools, **kwargs)
        else:
            payload = self._build_payload(messages, tools, **kwargs)
        url = self.config.endpoint

        async with session.post(url, json=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise self._classify_error(resp.status, text)
            data = json.loads(text)
            if use_responses:
                return self._parse_responses_response(data)
            return self._parse_response(data)

    async def chat_stream(self, messages, tools=None, **kwargs) -> AsyncIterator[StreamChunk]:
        session = await self._get_session()
        payload = self._build_payload(messages, tools, **kwargs)
        payload["stream"] = True
        url = self.config.endpoint

        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise self._classify_error(resp.status, text)
            async for line in resp.content:
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str.startswith("data: "):
                    continue
                data_str = line_str[6:]
                try:
                    data = json.loads(data_str)
                    chunk = self._parse_stream_chunk(data)
                    if chunk.content or chunk.tool_call_delta or chunk.finish_reason or chunk.usage:
                        yield chunk
                except json.JSONDecodeError:
                    continue


# ═══════════════════════════════════════════════════════════════
# Ollama Provider (本地模型)
# ═══════════════════════════════════════════════════════════════

class OllamaProvider(BaseProvider):
    """Ollama 本地模型"""

    def _build_headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def _build_payload(self, messages: list[dict], tools: list[dict] | None, **kwargs) -> dict:
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", self.config.temperature),
                "top_p": kwargs.get("top_p", self.config.top_p),
                "num_predict": kwargs.get("max_tokens", self.config.max_tokens),
            },
        }
        if tools:
            payload["tools"] = self._convert_tools(tools)
        return payload

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": t.get("function", t)} for t in tools]

    def _parse_response(self, data: dict) -> LLMResponse:
        message = data.get("message", {})
        tool_calls_raw = message.get("tool_calls", [])
        tool_calls = [{
            "id": tc.get("id", f"call_{i}"),
            "name": tc.get("function", {}).get("name", ""),
            "arguments": json.dumps(tc.get("function", {}).get("arguments", {}), ensure_ascii=False) if isinstance(tc.get("function", {}).get("arguments"), dict) else tc.get("function", {}).get("arguments", "{}"),
        } for i, tc in enumerate(tool_calls_raw)]

        return LLMResponse(
            content=message.get("content", ""),
            tool_calls=tool_calls,
            finish_reason=data.get("done_reason", "stop"),
            model=data.get("model", self.config.model),
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            },
            raw=data,
        )

    def _parse_stream_chunk(self, data: dict) -> StreamChunk:
        return StreamChunk(
            content=data.get("message", {}).get("content", ""),
            finish_reason="stop" if data.get("done") else None,
        )

    async def _chat_once(self, messages, tools, **kwargs) -> LLMResponse:
        session = await self._get_session()
        use_responses = self.config.wire_api == "responses"
        if use_responses:
            payload = self._build_responses_payload(messages, tools, **kwargs)
        else:
            payload = self._build_payload(messages, tools, **kwargs)
        url = self.config.endpoint

        async with session.post(url, json=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise self._classify_error(resp.status, text)
            data = json.loads(text)
            if use_responses:
                return self._parse_responses_response(data)
            return self._parse_response(data)

    async def chat_stream(self, messages, tools=None, **kwargs) -> AsyncIterator[StreamChunk]:
        session = await self._get_session()
        payload = self._build_payload(messages, tools, **kwargs)
        payload["stream"] = True
        url = self.config.endpoint

        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise self._classify_error(resp.status, text)
            buffer = ""
            async for line in resp.content:
                buffer += line.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line_str, buffer = buffer.split("\n", 1)
                    line_str = line_str.strip()
                    if not line_str:
                        continue
                    try:
                        chunk = self._parse_stream_chunk(json.loads(line_str))
                        if chunk.content or chunk.finish_reason:
                            yield chunk
                    except json.JSONDecodeError:
                        continue


# ═══════════════════════════════════════════════════════════════
# Provider Factory
# ═══════════════════════════════════════════════════════════════

_PROVIDER_REGISTRY: dict[ProviderKind, type[BaseProvider]] = {
    ProviderKind.OPENAI: OpenAIProvider,
    ProviderKind.CLAUDE: ClaudeProvider,
    ProviderKind.OLLAMA: OllamaProvider,
    ProviderKind.OPENROUTER: OpenAIProvider,  # OpenRouter 用 OpenAI 兼容接口
    ProviderKind.AZURE: OpenAIProvider,        # Azure 也用 OpenAI 兼容
    ProviderKind.DEEPSEEK: OpenAIProvider,     # DeepSeek uses OpenAI-compatible chat completions
    ProviderKind.CUSTOM: OpenAIProvider,
}


def create_provider(
    kind: ProviderKind | str,
    model: str = "gpt-4o",
    api_key: str = "",
    base_url: str = "",
    **kwargs,
) -> BaseProvider:
    """创建LLM Provider实例"""
    if isinstance(kind, str):
        kind = ProviderKind(kind)
    config = ProviderConfig(kind=kind, model=model, api_key=api_key, base_url=base_url, **kwargs)
    cls = _PROVIDER_REGISTRY.get(kind, OpenAIProvider)
    return cls(config)


# ═══════════════════════════════════════════════════════════════
# Provider Pool — 多Provider负载均衡 & 故障转移
# ═══════════════════════════════════════════════════════════════

@dataclass
class PoolProvider:
    provider: BaseProvider
    weight: int = 1
    healthy: bool = True
    failures: int = 0
    last_failure: float = 0.0
    cooldown_until: float = 0.0
    last_success: float = 0.0

    @property
    def available(self) -> bool:
        return self.healthy and time.time() >= self.cooldown_until


class ProviderPool:
    """多Provider池 — 加权轮询 + 健康检查 + 故障转移"""

    def __init__(self, providers: list[BaseProvider], cooldown_sec: float = 30.0, recovery_sec: float = 120.0):
        self._pool: list[PoolProvider] = []
        self._cooldown = cooldown_sec
        self._recovery_sec = recovery_sec
        self._max_weight = 10
        self._min_weight = 1
        self._round_robin_idx = 0
        for p in providers:
            self.add(p)

    def add(self, provider: BaseProvider, weight: int = 1):
        self._pool.append(PoolProvider(provider=provider, weight=weight))

    def remove(self, model: str):
        self._pool = [p for p in self._pool if p.provider.model != model]

    def _recover(self, pp: PoolProvider) -> None:
        """Auto-recover unhealthy providers after recovery interval."""
        if not pp.healthy and time.time() - pp.last_failure > self._recovery_sec:
            pp.healthy = True
            pp.failures = 0
            pp.weight = max(pp.weight, self._min_weight)

    @property
    def healthy_count(self) -> int:
        return sum(1 for p in self._pool if p.available)

    def _next_provider(self) -> PoolProvider | None:
        available = [p for p in self._pool if p.available]
        if not available:
            for pp in self._pool:
                self._recover(pp)
            available = [p for p in self._pool if p.available]
        if not available:
            return None
        # 加权轮询
        total_weight = sum(p.weight for p in available)
        if total_weight <= 0:
            return available[0] if available else None
        idx = self._round_robin_idx % total_weight
        self._round_robin_idx = (self._round_robin_idx + 1) % total_weight
        cumulative = 0
        for pp in available:
            cumulative += pp.weight
            if idx < cumulative:
                return pp
        return available[0]

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> LLMResponse:
        """发送请求，自动故障转移"""
        errors = []
        tried = set()

        for _ in range(len(self._pool)):
            for pp in self._pool:
                self._recover(pp)
            pp = self._next_provider()
            if pp is None or id(pp) in tried:
                break
            tried.add(id(pp))
            try:
                result = await pp.provider.chat(messages, tools, **kwargs)
                pp.failures = max(0, pp.failures - 1)
                pp.last_success = time.time()
                pp.healthy = True
                if pp.weight < self._max_weight:
                    pp.weight = min(self._max_weight, pp.weight + 1)
                return result
            except (RateLimitError, ServerOverloadError) as e:
                pp.failures += 1
                pp.last_failure = time.time()
                pp.cooldown_until = time.time() + self._cooldown
                pp.weight = max(self._min_weight, pp.weight - 1)
                errors.append(f"{pp.provider.model}: {e}")
            except ProviderError as e:
                pp.failures += 1
                pp.last_failure = time.time()
                if not e.retryable:
                    pp.healthy = False
                    pp.weight = self._min_weight
                else:
                    pp.cooldown_until = time.time() + self._cooldown
                    pp.weight = max(self._min_weight, pp.weight - 1)
                errors.append(f"{pp.provider.model}: {e}")

        raise ProviderError(f"All providers failed: {'; '.join(errors)}")

    async def chat_stream(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> AsyncIterator[StreamChunk]:
        errors = []
        tried = set()
        for _ in range(len(self._pool)):
            for pp in self._pool:
                self._recover(pp)
            pp = self._next_provider()
            if pp is None or id(pp) in tried:
                break
            tried.add(id(pp))
            try:
                async for chunk in pp.provider.chat_stream(messages, tools, **kwargs):
                    yield chunk
                pp.failures = max(0, pp.failures - 1)
                pp.last_success = time.time()
                pp.healthy = True
                if pp.weight < self._max_weight:
                    pp.weight = min(self._max_weight, pp.weight + 1)
                return
            except (RateLimitError, ServerOverloadError) as e:
                pp.failures += 1
                pp.last_failure = time.time()
                pp.cooldown_until = time.time() + self._cooldown
                pp.weight = max(self._min_weight, pp.weight - 1)
                errors.append(f"{pp.provider.model}: {e}")
            except ProviderError as e:
                pp.failures += 1
                pp.last_failure = time.time()
                if not e.retryable:
                    pp.healthy = False
                    pp.weight = self._min_weight
                else:
                    pp.cooldown_until = time.time() + self._cooldown
                    pp.weight = max(self._min_weight, pp.weight - 1)
                errors.append(f"{pp.provider.model}: {e}")
        raise ProviderError(f"All providers failed streaming: {'; '.join(errors)}")

    async def close(self):
        for pp in self._pool:
            await pp.provider.close()

    @property
    def stats(self) -> list[dict]:
        return [
            {**pp.provider.stats, "weight": pp.weight, "healthy": pp.healthy, "failures": pp.failures}
            for pp in self._pool
        ]