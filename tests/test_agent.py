# Tests for Agent core — state, pipeline, graph with MockLLM
import sys, asyncio, pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from backend.agent.state import AgentState, Message, PlanStep, ToolInvocation, ToolResult
from backend.agent.llm_client import MockLLMClient, LLMClient, LLMConfig
from backend.agent.llm_providers import LLMResponse, StreamChunk
from backend.agent.graph import AgentGraph
from backend.tools import tool_registry


class TestAgentState:
    def test_create_state(self):
        state = AgentState(session_id="s1", workspace=".")
        assert state.session_id == "s1"
        assert state.done == False
        assert state.total_turns == 0
        assert len(state.messages) == 0

    def test_add_message(self):
        state = AgentState(session_id="s1")
        state.add_message(Message.user("hello"))
        assert len(state.messages) == 1
        assert state.messages[0].role == "user"
        assert state.messages[0].content == "hello"

    def test_messages_as_openai(self):
        state = AgentState(session_id="s1")
        state.add_message(Message.system("sys"))
        state.add_message(Message.user("hi"))
        msgs = state.messages_as_openai()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"

    def test_plan_progress(self):
        state = AgentState(session_id="s1")
        state.plan = [
            PlanStep(step=1, description="a"),
            PlanStep(step=2, description="b"),
            PlanStep(step=3, description="c"),
        ]
        state.plan[0].complete("ok")
        state.plan[1].fail("err")
        prog = state.plan_progress()
        assert prog["total"] == 3
        assert prog["completed"] == 1
        assert prog["failed"] == 1
        assert prog["percentage"] == 33

    def test_clone_for_checkpoint(self):
        state = AgentState(session_id="s1")
        state.add_message(Message.user("test"))
        clone = state.clone_for_checkpoint()
        assert clone.session_id == "s1"
        assert len(clone.messages) == 1
        clone.messages[0].content = "modified"
        assert state.messages[0].content == "test"

    def test_to_from_dict(self):
        state = AgentState(session_id="s1", workspace=".")
        state.add_message(Message.user("hello"))
        state.plan = [PlanStep(step=1, description="do it")]
        d = state.to_dict()
        restored = AgentState.from_dict(d)
        assert restored.session_id == "s1"
        assert len(restored.messages) == 1
        assert len(restored.plan) == 1


class TestMockLLM:
    def test_chat_returns_llmresponse(self):
        llm = MockLLMClient()
        resp = asyncio.run(llm.chat([{"role": "user", "content": "hello"}]))
        assert isinstance(resp, LLMResponse)
        assert len(resp.content) > 0
        assert resp.model == "mock"

    def test_chat_stream(self):
        llm = MockLLMClient()
        async def test():
            chunks = []
            async for c in llm.chat_stream([{"role": "user", "content": "hello"}]):
                chunks.append(c)
            return chunks
        chunks = asyncio.run(test())
        assert len(chunks) > 0
        assert all(isinstance(c, StreamChunk) for c in chunks)

    def test_plan_response(self):
        llm = MockLLMClient()
        resp = asyncio.run(llm.chat([{"role": "user", "content": "帮我写一个计划"}]))
        assert isinstance(resp, LLMResponse)
        assert "step" in resp.content.lower() or "plan" in resp.content.lower()

    def test_chat_simple(self):
        llm = MockLLMClient()
        resp = asyncio.run(llm.chat_simple("hello world"))
        assert isinstance(resp, str)
        assert len(resp) > 0

    def test_token_counting(self):
        llm = MockLLMClient()
        tokens = llm.count_tokens("hello world test message")
        assert tokens > 0

    def test_embeddings(self):
        llm = MockLLMClient()
        embeds = asyncio.run(llm.embeddings(["test text"]))
        assert len(embeds) == 1
        assert len(embeds[0]) == 768


class TestAgentGraph:
    @pytest.fixture
    def graph(self):
        llm = MockLLMClient()
        async def tool_handler(name, args, ws):
            return {"success": True, "output": f"Mock output for {name}"}
        return AgentGraph(
            llm=llm,
            tool_handler=tool_handler,
            tools_schema=tool_registry.list_tools_openai(),
            max_turns=30,
            workspace=".",
        )

    @pytest.mark.asyncio
    async def test_run_completes(self, graph):
        state = await graph.run("hello", session_id="t1", workspace=".")
        assert state.done == True
        assert len(state.final_response) > 0
        assert len(state.messages) > 0

    @pytest.mark.asyncio
    async def test_run_with_plan(self, graph):
        state = await graph.run("help me write a plan for a todo app", session_id="t2", workspace=".")
        assert state.done == True
        assert state.plan is not None

    @pytest.mark.asyncio
    async def test_run_with_stream(self, graph):
        events = []
        async for ev in graph.run_with_stream("hello world", session_id="t3", workspace="."):
            events.append(ev)
        assert len(events) > 0
        assert events[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_max_turns_limit(self, graph):
        graph.max_turns = 1
        state = await graph.run("do many things", session_id="t4", workspace=".")
        assert state.done == True

    @pytest.mark.asyncio
    async def test_cancel(self, graph):
        await graph.cancel("t5")

    @pytest.mark.asyncio
    async def test_stats(self, graph):
        await graph.run("test", session_id="t6", workspace=".")
        stats = graph.stats()
        assert "llm" in stats
        assert "checkpoints" in stats
        assert "tools_count" in stats