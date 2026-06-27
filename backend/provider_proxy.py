# -*- coding: utf-8 -*-
"""Provider Format Proxy — Anthropic ⟷ OpenAI 格式互译层。

Port of cc-haha's src/server/proxy/ (13 files).
Real-time translation between Anthropic Messages / OpenAI Chat / OpenAI Responses.

Architecture:
  Request Translator   — model request before sending to API
  Response Translator  — API response back to internal format
  Streaming Translator — SSE stream conversion between formats
  Tool Translator      — tool definitions between Anthropic/OpenAI schemas
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Iterator, Optional

logger = logging.getLogger("aurora.proxy")

# ── Content Block Types ─────────────────────────────────────────

TEXT_BLOCK = "text"
TOOL_USE_BLOCK = "tool_use"
TOOL_RESULT_BLOCK = "tool_result"
IMAGE_BLOCK = "image"
THINKING_BLOCK = "thinking"


class ProviderFormat(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"


# ── Tool Translation ────────────────────────────────────────────

def anthropic_tool_to_openai(tool: dict) -> dict:
    """Convert Anthropic tool definition → OpenAI function definition."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def openai_tool_to_anthropic(tool: dict) -> dict:
    """Convert OpenAI function definition → Anthropic tool definition."""
    func = tool.get("function", {})
    return {
        "name": func.get("name", ""),
        "description": func.get("description", ""),
        "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
    }


def translate_tools(tools: list[dict], target: ProviderFormat, source: ProviderFormat = ProviderFormat.ANTHROPIC) -> list[dict]:
    """Batch translate tools between formats."""
    if target == source:
        return tools

    if source == ProviderFormat.ANTHROPIC and target == ProviderFormat.OPENAI_CHAT:
        return [anthropic_tool_to_openai(t) for t in tools]
    elif source == ProviderFormat.OPENAI_CHAT and target == ProviderFormat.ANTHROPIC:
        return [openai_tool_to_anthropic(t) for t in tools]
    return tools


# ── Message Translation ─────────────────────────────────────────

def anthropic_to_openai_chat(messages: list[dict], system: str = "") -> list[dict]:
    """Convert Anthropic Messages → OpenAI Chat Completions format."""
    result = []

    # System prompt
    if system:
        result.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            result.append({"role": _map_role_anth_to_oai(role), "content": content})
        elif isinstance(content, list):
            # Multi-block content
            text_parts = []
            tool_calls = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })
                elif block.get("type") == "tool_result":
                    result.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": _block_content_text(block),
                    })
                elif block.get("type") == "image":
                    text_parts.append("[Image]")

            oai_role = _map_role_anth_to_oai(role)
            if tool_calls:
                result.append({
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                    "tool_calls": tool_calls,
                })
            elif text_parts:
                result.append({"role": oai_role, "content": "\n".join(text_parts)})

    return result


def openai_chat_to_anthropic(messages: list[dict], system: str = "") -> tuple[list[dict], str]:
    """Convert OpenAI Chat → Anthropic Messages format + extract system prompt."""
    result = []
    extracted_system = system

    for msg in messages:
        role = msg.get("role", "user")

        if role == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                extracted_system = (extracted_system + "\n\n" + content).strip()
            continue

        if role == "tool":
            result.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": _content_to_string(msg.get("content", "")),
                }],
            })
            continue

        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        if tool_calls:
            blocks = []
            if content:
                blocks.append({"type": "text", "text": _content_to_string(content)})
            for tc in tool_calls:
                func = tc.get("function", {})
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": func.get("name", ""),
                    "input": args,
                })
            result.append({"role": "assistant", "content": blocks})
        else:
            result.append({
                "role": _map_role_oai_to_anth(role),
                "content": _content_to_string(content),
            })

    return result, extracted_system


# ── Streaming Translation ───────────────────────────────────────

def translate_stream_chunk(chunk: dict, target: ProviderFormat, model: str = "") -> dict:
    """Translate a single streaming chunk between formats.

    Anthropic SSE: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "..."}}
    OpenAI SSE:    {"choices": [{"delta": {"content": "..."}}]}

    Returns translated chunk dict, or {} if chunk should be skipped.
    """
    chunk_type = chunk.get("type", "") or chunk.get("object", "")

    if target == ProviderFormat.OPENAI_CHAT:
        return _anth_chunk_to_openai(chunk, model)
    elif target == ProviderFormat.ANTHROPIC:
        return _oai_chunk_to_anthropic(chunk)
    return chunk


async def translate_sse_stream(
    source_stream: AsyncIterator[str],
    target: ProviderFormat,
    model: str = "",
) -> AsyncIterator[str]:
    """Translate an SSE stream line by line."""
    async for line in source_stream:
        line = line.strip()
        if not line or not line.startswith("data:"):
            yield line + "\n"
            continue

        data_str = line[5:].strip()
        if data_str == "[DONE]":
            yield "data: [DONE]\n"
            continue

        try:
            chunk = json.loads(data_str)
            translated = translate_stream_chunk(chunk, target, model)
            if translated:
                yield f"data: {json.dumps(translated)}\n"
        except json.JSONDecodeError:
            yield line + "\n"


def sync_translate_sse_stream(
    source_lines: Iterator[str],
    target: ProviderFormat,
    model: str = "",
) -> Iterator[str]:
    """Translate an SSE stream synchronously (line by line)."""
    for line in source_lines:
        line = line.strip()
        if not line or not line.startswith("data:"):
            yield line + "\n"
            continue

        data_str = line[5:].strip()
        if data_str == "[DONE]":
            yield "data: [DONE]\n"
            continue

        try:
            chunk = json.loads(data_str)
            translated = translate_stream_chunk(chunk, target, model)
            if translated:
                yield f"data: {json.dumps(translated)}\n"
        except json.JSONDecodeError:
            yield line + "\n"


# ── Response Format Translation ─────────────────────────────────

def anthropic_response_to_openai(response: dict, model: str = "") -> dict:
    """Translate Anthropic Messages response → OpenAI Chat format."""
    choices = []
    content_blocks = response.get("content", [])

    text_parts = []
    tool_calls = []

    for block in content_blocks:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })

    message = {"role": "assistant", "content": "\n".join(text_parts) if text_parts else None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    choices.append({
        "index": 0,
        "message": message,
        "finish_reason": _map_stop_reason(response.get("stop_reason", "")),
    })

    usage = response.get("usage", {})
    return {
        "id": response.get("id", ""),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or response.get("model", ""),
        "choices": choices,
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


def openai_response_to_anthropic(response: dict) -> dict:
    """Translate OpenAI Chat response → Anthropic Messages format."""
    choices = response.get("choices", [])
    content = []

    for choice in choices:
        msg = choice.get("message", {}) or choice.get("delta", {})

        text = msg.get("content", "")
        if text:
            content.append({"type": "text", "text": text})

        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            content.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "input": args,
            })

    usage = response.get("usage", {})
    return {
        "id": response.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": response.get("model", ""),
        "content": content,
        "stop_reason": _map_finish_reason(
            choices[0].get("finish_reason", "") if choices else ""
        ),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── Billing Header Tracking ────────────────────────────────────

@dataclass
class BillingInfo:
    """Track billing info from response headers."""
    request_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str = ""
    provider: ProviderFormat = ProviderFormat.ANTHROPIC

    @classmethod
    def from_anthropic_headers(cls, headers: dict) -> "BillingInfo":
        return cls(
            request_id=headers.get("request-id", ""),
            input_tokens=int(headers.get("anthropic-ratelimit-input-tokens", 0)),
            output_tokens=int(headers.get("anthropic-ratelimit-output-tokens", 0)),
            cache_read_tokens=int(headers.get("anthropic-ratelimit-cache-read-input-tokens", 0)),
            cache_write_tokens=int(headers.get("anthropic-ratelimit-cache-creation-input-tokens", 0)),
            provider=ProviderFormat.ANTHROPIC,
        )

    @classmethod
    def from_openai_headers(cls, headers: dict) -> "BillingInfo":
        return cls(
            request_id=headers.get("x-request-id", ""),
            input_tokens=int(headers.get("x-ratelimit-prompt-tokens", 0)),
            output_tokens=int(headers.get("x-ratelimit-completion-tokens", 0)),
            provider=ProviderFormat.OPENAI_CHAT,
        )

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }


# ── Prompt Cache Key ────────────────────────────────────────────

def compute_prompt_cache_key(messages: list[dict], system: str = "", tools: list[dict] | None = None, max_tokens: int = 0) -> str:
    """Compute a stable prompt cache key for cache-breaking decisions."""
    import hashlib

    payload = {
        "messages": _truncate_for_cache(messages, 3),
        "system": system[:500],
        "tools": len(tools or []),
        "max_tokens": max_tokens,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Internal Helpers ────────────────────────────────────────────

def _map_role_anth_to_oai(role: str) -> str:
    mapping = {"user": "user", "assistant": "assistant"}
    return mapping.get(role, "user")


def _map_role_oai_to_anth(role: str) -> str:
    mapping = {"user": "user", "assistant": "assistant", "system": "user"}
    return mapping.get(role, "user")


def _map_stop_reason(reason: str) -> str:
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }
    return mapping.get(reason, "stop")


def _map_finish_reason(reason: str) -> str:
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }
    return mapping.get(reason, "end_turn")


def _content_to_string(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image_url":
                    parts.append("[Image]")
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _block_content_text(block: dict) -> str:
    content = block.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _content_to_string(content)
    return str(content)


def _truncate_for_cache(messages: list[dict], max_count: int) -> list[dict]:
    if len(messages) <= max_count:
        return messages
    # Keep first and last
    return [messages[0], {"role": "system", "content": f"[{len(messages) - 2} messages omitted]"}, messages[-1]]


def _anth_chunk_to_openai(chunk: dict, model: str) -> dict | None:
    """Translate Anthropic streaming chunk → OpenAI SSE chunk."""
    ctype = chunk.get("type", "")

    if ctype == "message_start":
        return {
            "id": chunk.get("message", {}).get("id", ""),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model or chunk.get("message", {}).get("model", ""),
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    elif ctype == "content_block_delta":
        delta = chunk.get("delta", {})
        if delta.get("type") == "text_delta":
            return {
                "id": "",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": delta.get("text", "")}, "finish_reason": None}],
            }
        elif delta.get("type") == "input_json_delta":
            return {
                "id": "",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"tool_calls": [{"index": chunk.get("index", 0), "function": {"arguments": delta.get("partial_json", "")}}]}, "finish_reason": None}],
            }
    elif ctype == "message_delta":
        stop_reason = chunk.get("delta", {}).get("stop_reason", "")
        return {
            "id": "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": _map_stop_reason(stop_reason)}],
        }
    elif ctype == "message_stop":
        return None  # Skip — OpenAI doesn't have this

    return None


def _oai_chunk_to_anthropic(chunk: dict) -> dict | None:
    """Translate OpenAI streaming chunk → Anthropic SSE event."""
    choices = chunk.get("choices", [])
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    finish = choice.get("finish_reason")

    # Start of stream
    if delta.get("role") == "assistant" and not delta.get("content"):
        return {
            "type": "message_start",
            "message": {
                "id": chunk.get("id", ""),
                "type": "message",
                "role": "assistant",
                "model": chunk.get("model", ""),
                "content": [],
            },
        }

    # Text delta
    if delta.get("content"):
        return {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": delta["content"]},
        }

    # Tool call delta
    if delta.get("tool_calls"):
        tc = delta["tool_calls"][0]
        return {
            "type": "content_block_delta",
            "index": tc.get("index", 0),
            "delta": {
                "type": "input_json_delta",
                "partial_json": tc.get("function", {}).get("arguments", ""),
            },
        }

    # Finish
    if finish:
        return {
            "type": "message_delta",
            "delta": {"stop_reason": _map_finish_reason(finish)},
            "usage": {"output_tokens": 0},
        }

    return None
