"""Aurora API - Pydantic models."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Optional


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    workspace: str = "."
    stream: bool = False
    sandbox_mode: str = "full-access"
    model: str = ""
    history: list[dict] | None = None


class AgentResponse(BaseModel):
    session_id: str
    response: str
    plan: list[dict] = []
    diffs: list[str] = []
    tokens: int = 0


class IndexRequest(BaseModel):
    path: str



class SemanticIndexRequest(BaseModel):
    text: str
    metadata: dict = {}


class RenderPromptRequest(BaseModel):
    name: str
    variables: dict = {}


class AutoPromptRequest(BaseModel):
    input: str


class ConfigUpdateRequest(BaseModel):
    key: str
    value: Any


class SettingsUpdate(BaseModel):
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    max_context_tokens: int | None = None
    max_turn_iter: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    vision_fallback: dict | None = None


class LLMTestRequest(BaseModel):
    message: str = "Hello, say connected in one short sentence."


class MarketplaceInstallRequest(BaseModel):
    repo_url: str


class SentryConfig(BaseModel):
    enabled: bool = False
    dsn: str = ""


class TaskSubmitRequest(BaseModel):
    type: str
    path: str = ""
    workspace: str = "."
