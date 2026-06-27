import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.agent.graph import AgentGraph
from backend.agent.llm_client import MockLLMClient
from backend.agent.llm_providers import LLMResponse
from backend.agent.nodes import tool_select_node
from backend.agent.state import AgentState, Message
from backend.api import deps
from backend.api.models import ChatRequest
from backend.api.routes import chat as chat_routes
from backend.approval import ApprovalBridge, ApprovalManager, ApprovalPolicy, RiskLevel
from backend.tools.base import ToolRegistry, ToolSpec
from backend.tools.shell_command import shell_handler


class ToolCallingLLM(MockLLMClient):
    async def chat(self, messages, tools=None, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[{"name": "list_files", "arguments": json.dumps({"path": "."})}],
            finish_reason="tool_calls",
            model="mock",
        )


@pytest.mark.asyncio
async def test_tool_select_accepts_normalized_provider_tool_calls():
    state = AgentState(session_id="tool-call-format")
    state.add_message(Message.user("list files"))
    state.plan = []

    await tool_select_node(
        state,
        ToolCallingLLM(),
        [{"type": "function", "function": {"name": "list_files", "parameters": {"type": "object"}}}],
    )

    assert len(state.tool_invocations) == 1
    assert state.tool_invocations[0].name == "list_files"
    assert state.tool_invocations[0].arguments == {"path": "."}


@pytest.mark.asyncio
async def test_tool_registry_propagates_dict_failure_result():
    registry = ToolRegistry()

    async def failing_handler(args, workspace):
        return {"success": False, "stdout": "", "stderr": "boom", "exit_code": 1}

    registry.register(ToolSpec(name="failing", description="fails", parameters={"type": "object"}), failing_handler)

    result = await registry.execute("failing", {}, ".")

    assert result.success is False
    assert result.error == "boom"
    assert "exit_code" in result.metadata


@pytest.mark.asyncio
async def test_shell_handler_blocks_until_approval_decision(monkeypatch, tmp_path):
    events = []
    manager = ApprovalManager(policy=ApprovalPolicy.ON_REQUEST)

    async def emit(event):
        events.append(event.to_dict())
        request_id = event.to_dict()["data"]["request_id"]
        manager.deny(request_id)

    bridge = ApprovalBridge(manager=manager, event_emit=emit)
    monkeypatch.setattr("backend.approval.approval_bridge", bridge)

    result = await shell_handler(
        {"command": "echo should_not_run", "timeout": 5, "session_id": "s1", "thread_id": "t1"},
        workspace=str(tmp_path),
    )

    assert result["success"] is False
    assert "denied" in result["stderr"].lower()
    assert events[0]["type"] == "codex/event/exec_approval_request"


def test_get_llm_uses_configured_provider(monkeypatch):
    class FakeConfig:
        llm_model = "deepseek-chat"
        llm_api_key = "test-key"
        llm_base_url = "https://api.deepseek.com"
        max_turn_iter = 30

        def get(self, key, default=None):
            return {"llm.provider": "deepseek"}.get(key, default)

    deps.reset_deps()
    monkeypatch.setattr(deps, "get_config", lambda: FakeConfig())
    llm = deps.get_llm()

    assert llm.config.provider == "deepseek"
    deps.reset_deps()


@pytest.mark.asyncio
async def test_chat_route_passes_model_and_sandbox_to_graph(monkeypatch):
    captured = {}

    class FakeGraph:
        async def run(self, user_input, session_id="", workspace=".", sandbox_mode="full-access", approval_mode="never", model="", history=None):
            captured.update({
                "user_input": user_input,
                "session_id": session_id,
                "workspace": workspace,
                "sandbox_mode": sandbox_mode,
                "approval_mode": approval_mode,
                "model": model,
                "history": history,
            })
            state = AgentState(session_id=session_id, workspace=workspace)
            state.final_response = "ok"
            return state

    class FakeSkills:
        def match(self, message):
            return []

        def inject(self, triggered):
            return ""

    class FakeRag:
        class Store:
            def count(self):
                return 0

        vector_store = Store()

    monkeypatch.setattr(chat_routes, "_get_graph", lambda: FakeGraph())
    monkeypatch.setattr(chat_routes, "_get_skills", lambda: FakeSkills())
    monkeypatch.setattr(chat_routes, "_get_rag", lambda: FakeRag())

    req = ChatRequest(
        message="hello",
        session_id="s1",
        workspace="D:/workspace",
        sandbox_mode="read-only",
        approval_mode="on-request",
        model="deepseek-chat",
        history=[{"role": "user", "content": "old"}],
    )

    response = await chat_routes.chat(req)

    assert response.response == "ok"
    assert captured["sandbox_mode"] == "read-only"
    assert captured["approval_mode"] == "on-request"
    assert captured["model"] == "deepseek-chat"
    assert captured["workspace"] == "D:/workspace"


@pytest.mark.asyncio
async def test_chat_stream_injects_skills_and_rag_and_passes_options(monkeypatch):
    captured = {}

    class FakeGraph:
        async def run_with_stream(self, user_input, session_id="", workspace=".", sandbox_mode="full-access", approval_mode="never", model="", history=None):
            captured.update({
                "user_input": user_input,
                "session_id": session_id,
                "workspace": workspace,
                "sandbox_mode": sandbox_mode,
                "approval_mode": approval_mode,
                "model": model,
                "history": history,
            })
            yield {"type": "done", "response": "ok"}

    class FakeSkills:
        def match(self, message):
            return ["skill"]

        def inject(self, triggered):
            return "SKILL_CTX\n"

    class FakeRag:
        class Store:
            def count(self):
                return 1

        vector_store = Store()

        def search(self, message, top_k=5, llm_client=None):
            return [{"content": "rag", "metadata": {}}]

        def format_context(self, chunks):
            return "RAG_CTX\n"

    monkeypatch.setattr(chat_routes, "_get_graph", lambda: FakeGraph())
    monkeypatch.setattr(chat_routes, "_get_skills", lambda: FakeSkills())
    monkeypatch.setattr(chat_routes, "_get_rag", lambda: FakeRag())
    monkeypatch.setattr(chat_routes, "_llm", None)

    req = ChatRequest(
        message="hello",
        session_id="s1",
        workspace="D:/workspace",
        sandbox_mode="read-only",
        approval_mode="on-request",
        model="deepseek-chat",
    )

    stream = await chat_routes.chat_stream(req)
    chunks = []
    async for chunk in stream.body_iterator:
        chunks.append(chunk)

    assert "SKILL_CTX" in captured["user_input"]
    assert "RAG_CTX" in captured["user_input"]
    assert "User: hello" in captured["user_input"]
    assert captured["sandbox_mode"] == "read-only"
    assert captured["approval_mode"] == "on-request"
    assert captured["model"] == "deepseek-chat"
    assert chunks
