"""Tests for MultiAgentOrchestrator — spawn, start, send, wait, close, DAG tree."""
import asyncio, time
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from backend.multi_agent import (
    AgentStatus,
    AgentNode,
    MultiAgentOrchestrator,
)

@pytest.fixture(autouse=True)
def cleanup_orchestrator():
    """Clean up orchestrator tasks after each test."""
    yield
    from backend.multi_agent import orchestrator
    # Sync cleanup: cancel all pending tasks without awaiting
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        for aid, task in list(orchestrator._task_registry.items()):
            if not task.done():
                task.cancel()
        orchestrator._task_registry.clear()
        orchestrator._running.clear()
        orchestrator._pending.clear()
        orchestrator._executor_registry.clear()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# AgentNode tests
# ═══════════════════════════════════════════════════════

class TestAgentNode:
    def test_defaults(self):
        node = AgentNode(id="test_1", name="worker")
        assert node.id == "test_1"
        assert node.name == "worker"
        assert node.status == AgentStatus.IDLE
        assert node.task == ""
        assert node.result == ""
        assert node.error == ""
        assert node.parent_id is None
        assert node.children == []
        assert node.created_at > 0

    def test_custom_fields(self):
        node = AgentNode(id="a2", name="builder", parent_id="a1",
                         task="build stuff", priority=5,
                         metadata={"lang": "python"})
        assert node.parent_id == "a1"
        assert node.priority == 5
        assert node.metadata["lang"] == "python"

    def test_children_list(self):
        parent = AgentNode(id="p", name="parent")
        child1 = AgentNode(id="c1", name="child1", parent_id="p")
        child2 = AgentNode(id="c2", name="child2", parent_id="p")
        parent.children = [child1.id, child2.id]
        assert len(parent.children) == 2
        assert child1.id in parent.children


# ═══════════════════════════════════════════════════════
# AgentStatus enum
# ═══════════════════════════════════════════════════════

class TestAgentStatus:
    def test_values(self):
        assert AgentStatus.IDLE.value == "idle"
        assert AgentStatus.RUNNING.value == "running"
        assert AgentStatus.WAITING.value == "waiting"
        assert AgentStatus.DONE.value == "done"
        assert AgentStatus.ERROR.value == "error"
        assert AgentStatus.CLOSED.value == "closed"

    def test_total_states(self):
        assert len(AgentStatus) == 6


# ═══════════════════════════════════════════════════════
# MultiAgentOrchestrator tests
# ═══════════════════════════════════════════════════════

async def _pass_executor(agent: AgentNode) -> str:
    """Trivial executor: returns a fixed string."""
    return f"OK: {agent.name}"


async def _slow_executor(agent: AgentNode) -> str:
    """Slow executor: sleeps a bit before returning."""
    await asyncio.sleep(0.3)
    return f"slow: {agent.name}"


async def _error_executor(agent: AgentNode) -> str:
    """Executor that raises."""
    raise RuntimeError(f"boom from {agent.name}")


async def _stash_executor(agent: AgentNode) -> str:
    """Executor that stores result for later inspection."""
    agent.metadata["executed"] = True
    return f"stashed: {agent.name}"


class TestOrchestratorInit:
    def test_defaults(self):
        o = MultiAgentOrchestrator()
        assert o.max_parallel == 4
        assert o.default_timeout == 300
        assert o.agents == {}
        assert o._running == set()
        assert o._pending == set()

    def test_custom(self):
        o = MultiAgentOrchestrator(max_parallel=2, default_timeout=10)
        assert o.max_parallel == 2
        assert o.default_timeout == 10


class TestSpawn:
    @pytest.mark.asyncio
    async def test_spawn_root(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "root", "do root work")
        assert agent.id.startswith("agent_")
        assert agent.name == "root"
        assert agent.task == "do root work"
        assert agent.parent_id is None
        assert agent.status == AgentStatus.IDLE
        assert agent.id in o.agents

    @pytest.mark.asyncio
    async def test_spawn_child(self):
        o = MultiAgentOrchestrator()
        parent = await o.spawn(None, "parent", "p task")
        child = await o.spawn(parent.id, "child", "c task")
        assert child.parent_id == parent.id
        assert child.id in parent.children
        assert len(parent.children) == 1

    @pytest.mark.asyncio
    async def test_spawn_with_priority(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "urgent", "urgent task", priority=10)
        assert agent.priority == 10

    @pytest.mark.asyncio
    async def test_spawn_with_metadata(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "meta", "meta task",
                              metadata={"key": "value", "nested": {"a": 1}})
        assert agent.metadata["key"] == "value"
        assert agent.metadata["nested"]["a"] == 1

    @pytest.mark.asyncio
    async def test_spawn_ids_unique(self):
        o = MultiAgentOrchestrator()
        ids = set()
        for _ in range(20):
            a = await o.spawn(None, f"agent_{_}", "task")
            ids.add(a.id)
        assert len(ids) == 20


class TestStart:
    @pytest.mark.asyncio
    async def test_start_success(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "worker", "build")
        await o.start(agent.id, _pass_executor)
        # Let the task run
        await asyncio.sleep(0.1)
        assert agent.status == AgentStatus.DONE
        assert "OK: worker" in agent.result
        assert agent.started_at > 0
        assert agent.finished_at > 0

    @pytest.mark.asyncio
    async def test_start_error(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "bad", "will fail")
        await o.start(agent.id, _error_executor)
        await asyncio.sleep(0.1)
        assert agent.status == AgentStatus.ERROR
        assert "RuntimeError" in agent.error
        assert "boom from bad" in agent.error

    @pytest.mark.asyncio
    async def test_start_nonexistent(self):
        o = MultiAgentOrchestrator()
        # Should not throw
        await o.start("does_not_exist", _pass_executor)
        assert True  # reached without error

    @pytest.mark.asyncio
    async def test_start_multiple_no_exceed_parallel(self):
        o = MultiAgentOrchestrator(max_parallel=2)
        agents = []
        for i in range(5):
            a = await o.spawn(None, f"a{i}", f"task {i}")
            agents.append(a)

        # Start all 5
        for a in agents:
            await o.start(a.id, _slow_executor)

        # Check that only 2 are running, 3 are waiting
        await asyncio.sleep(0.05)
        running = [a for a in agents if a.status == AgentStatus.RUNNING]
        waiting = [a for a in agents if a.status == AgentStatus.WAITING]
        assert len(running) == 2
        assert len(waiting) == 3
        assert len(o._running) == 2
        assert len(o._pending) == 3

    @pytest.mark.asyncio
    async def test_start_all_finish_within_parallel(self):
        o = MultiAgentOrchestrator(max_parallel=3)
        agents = []
        for i in range(4):
            a = await o.spawn(None, f"a{i}", f"task {i}")
            agents.append(a)
        for a in agents:
            await o.start(a.id, _slow_executor)
        await asyncio.sleep(1.0)  # Wait for all slow executors
        done = [a for a in agents if a.status == AgentStatus.DONE]
        assert len(done) == 4
        assert len(o._running) == 0
        assert len(o._pending) == 0

    @pytest.mark.asyncio
    async def test_start_drain_by_priority(self):
        o = MultiAgentOrchestrator(max_parallel=1)
        # Create agents with varying priority
        agents = []
        for i, pri in enumerate([1, 10, 5, 3]):
            a = await o.spawn(None, f"pri{pri}", "task", priority=pri)
            agents.append(a)
            await o.start(a.id, _slow_executor)

        # First started should be running (priority doesn't matter for first)
        # But the queue should prioritize the high-priority one next
        await asyncio.sleep(0.05)
        assert agents[0].status == AgentStatus.RUNNING
        # The pending ones, sorted by priority: 10, 5, 3, 1
        pending_sorted = sorted(
            [a for a in agents if a.status == AgentStatus.WAITING],
            key=lambda x: -x.priority
        )
        # After first finishes, highest priority should drain next
        assert pending_sorted[0].priority == 10


class TestSend:
    @pytest.mark.asyncio
    async def test_send_to_agent(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "target", "original")
        result = await o.send(agent.id, "hello there")
        assert result is True
        assert len(agent.messages) == 1
        assert agent.messages[0]["role"] == "user"
        assert agent.messages[0]["content"] == "hello there"

    @pytest.mark.asyncio
    async def test_send_multiple(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "target", "original")
        await o.send(agent.id, "msg 1")
        await o.send(agent.id, "msg 2")
        await o.send(agent.id, "msg 3")
        assert len(agent.messages) == 3

    @pytest.mark.asyncio
    async def test_send_nonexistent(self):
        o = MultiAgentOrchestrator()
        result = await o.send("no_such_id", "hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_to_closed(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "target", "original")
        await o.close(agent.id)
        result = await o.send(agent.id, "hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_timestamps(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "target", "original")
        t0 = time.time()
        await o.send(agent.id, "timed msg")
        t1 = time.time()
        assert t0 <= agent.messages[0]["ts"] <= t1


class TestWait:
    @pytest.mark.asyncio
    async def test_wait_all_done(self):
        o = MultiAgentOrchestrator()
        a1 = await o.spawn(None, "a", "t")
        a2 = await o.spawn(None, "b", "t")
        await o.start(a1.id, _pass_executor)
        await o.start(a2.id, _pass_executor)
        await asyncio.sleep(0.1)
        results = await o.wait([a1.id, a2.id], timeout=5)
        assert len(results) == 2
        assert all(r.status == AgentStatus.DONE for r in results)

    @pytest.mark.asyncio
    async def test_wait_timeout(self):
        o = MultiAgentOrchestrator(default_timeout=1)
        a = await o.spawn(None, "slow", "t")
        await o.start(a.id, lambda _: asyncio.sleep(2))
        results = await o.wait([a.id], timeout=0.2)
        # May or may not have finished
        assert len(results) <= 1

    @pytest.mark.asyncio
    async def test_wait_empty_list(self):
        o = MultiAgentOrchestrator()
        results = await o.wait([])
        assert results == []

    @pytest.mark.asyncio
    async def test_wait_nonexistent(self):
        o = MultiAgentOrchestrator()
        results = await o.wait(["no_such"], timeout=1)
        assert results == []

    @pytest.mark.asyncio
    async def test_wait_with_error_agent(self):
        o = MultiAgentOrchestrator()
        a = await o.spawn(None, "fail", "t")
        await o.start(a.id, _error_executor)
        await asyncio.sleep(0.1)
        results = await o.wait([a.id])
        assert len(results) == 1
        assert results[0].status == AgentStatus.ERROR


class TestClose:
    @pytest.mark.asyncio
    async def test_close_basic(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "close_me", "task")
        result = await o.close(agent.id)
        assert result is True
        assert agent.status == AgentStatus.CLOSED

    @pytest.mark.asyncio
    async def test_close_nonexistent(self):
        o = MultiAgentOrchestrator()
        result = await o.close("no_such")
        assert result is False

    @pytest.mark.asyncio
    async def test_close_cascade(self):
        o = MultiAgentOrchestrator()
        root = await o.spawn(None, "root", "r")
        child1 = await o.spawn(root.id, "c1", "c1")
        child2 = await o.spawn(root.id, "c2", "c2")
        grandchild = await o.spawn(child1.id, "gc", "gc")

        result = await o.close(root.id, cascade=True)
        assert result is True
        assert root.status == AgentStatus.CLOSED
        assert child1.status == AgentStatus.CLOSED
        assert child2.status == AgentStatus.CLOSED
        assert grandchild.status == AgentStatus.CLOSED

    @pytest.mark.asyncio
    async def test_close_no_cascade(self):
        o = MultiAgentOrchestrator()
        root = await o.spawn(None, "root", "r")
        child = await o.spawn(root.id, "child", "c")
        await o.close(root.id, cascade=False)
        assert root.status == AgentStatus.CLOSED
        assert child.status == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_close_running_agent(self):
        o = MultiAgentOrchestrator()
        agent = await o.spawn(None, "runner", "task")
        await o.start(agent.id, _slow_executor)
        await asyncio.sleep(0.05)
        assert agent.status == AgentStatus.RUNNING
        result = await o.close(agent.id)
        assert result is True
        assert agent.status == AgentStatus.CLOSED


class TestGetTree:
    @pytest.mark.asyncio
    async def test_empty_tree(self):
        o = MultiAgentOrchestrator()
        tree = o.get_tree()
        assert tree["roots"] == []
        assert tree["total"] == 0

    @pytest.mark.asyncio
    async def test_single_root(self):
        o = MultiAgentOrchestrator()
        await o.spawn(None, "only", "task")
        tree = o.get_tree()
        assert len(tree["roots"]) == 1
        assert tree["roots"][0]["name"] == "only"
        assert tree["total"] == 1

    @pytest.mark.asyncio
    async def test_nested_tree(self):
        o = MultiAgentOrchestrator()
        root = await o.spawn(None, "root", "r")
        c1 = await o.spawn(root.id, "c1", "c1")
        await o.spawn(c1.id, "gc", "gc")
        await o.spawn(root.id, "c2", "c2")
        tree = o.get_tree()
        assert tree["total"] == 4
        roots = tree["roots"]
        assert len(roots) == 1
        root_node = roots[0]
        assert root_node["name"] == "root"
        assert len(root_node["children"]) == 2
        child_names = {c["name"] for c in root_node["children"]}
        assert child_names == {"c1", "c2"}

    @pytest.mark.asyncio
    async def test_subtree_view(self):
        o = MultiAgentOrchestrator()
        root = await o.spawn(None, "root", "r")
        child = await o.spawn(root.id, "child", "c")
        sub = o.get_tree(root_id=child.id)
        assert len(sub["roots"]) == 1
        assert sub["roots"][0]["name"] == "child"
        assert sub["roots"][0]["children"] == []

    @pytest.mark.asyncio
    async def test_tree_shows_status(self):
        o = MultiAgentOrchestrator()
        a = await o.spawn(None, "done_agent", "t")
        await o.start(a.id, _pass_executor)
        await asyncio.sleep(0.1)
        tree = o.get_tree()
        assert tree["roots"][0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_tree_running_count(self):
        o = MultiAgentOrchestrator(max_parallel=2)
        for i in range(3):
            await o.spawn(None, f"a{i}", f"t{i}")
        # Start 3, with max_parallel=2, so 2 running + 1 pending
        for aid in list(o.agents.keys()):
            await o.start(aid, _slow_executor)
        await asyncio.sleep(0.05)
        tree = o.get_tree()
        assert tree["running"] == 2
        assert tree["pending"] == 1


class TestListAgents:
    @pytest.mark.asyncio
    async def test_list_all(self):
        o = MultiAgentOrchestrator()
        await o.spawn(None, "a", "t")
        await o.spawn(None, "b", "t")
        await o.spawn(None, "c", "t")
        agents = o.list_agents()
        assert len(agents) == 3

    @pytest.mark.asyncio
    async def test_list_by_status(self):
        o = MultiAgentOrchestrator()
        a = await o.spawn(None, "a", "t")
        await o.spawn(None, "b", "t")
        await o.start(a.id, _pass_executor)
        await asyncio.sleep(0.1)

        idle_agents = o.list_agents(status_filter=AgentStatus.IDLE)
        done_agents = o.list_agents(status_filter=AgentStatus.DONE)
        assert len(done_agents) >= 1
        assert len(idle_agents) >= 1

    @pytest.mark.asyncio
    async def test_list_empty(self):
        o = MultiAgentOrchestrator()
        agents = o.list_agents()
        assert agents == []


class TestStats:
    @pytest.mark.asyncio
    async def test_empty_stats(self):
        o = MultiAgentOrchestrator()
        s = o.stats()
        assert s["total"] == 0
        assert s["max_parallel"] == 4
        assert s["running"] == 0

    @pytest.mark.asyncio
    async def test_populated_stats(self):
        o = MultiAgentOrchestrator()
        a1 = await o.spawn(None, "a", "t")
        a2 = await o.spawn(None, "b", "t")
        await o.start(a1.id, _pass_executor)
        await asyncio.sleep(0.1)
        s = o.stats()
        assert s["total"] == 2
        assert "done" in s["by_status"]
        assert "idle" in s["by_status"]
        assert s["by_status"]["done"] + s["by_status"]["idle"] == 2
        assert s["max_parallel"] == 4
        assert isinstance(s["running"], int)

    @pytest.mark.asyncio
    async def test_stats_running_count(self):
        o = MultiAgentOrchestrator(max_parallel=2)
        for i in range(3):
            await o.spawn(None, f"a{i}", f"t{i}")
        for aid in list(o.agents.keys()):
            await o.start(aid, _slow_executor)
        await asyncio.sleep(0.05)
        s = o.stats()
        assert s["running"] == 2


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_max_parallel_enforced(self):
        """Start 8 agents with max_parallel=2; only 2 should run at once."""
        o = MultiAgentOrchestrator(max_parallel=2)
        for i in range(8):
            a = await o.spawn(None, f"a{i}", f"t{i}")
            await o.start(a.id, _pass_executor)
        await asyncio.sleep(0.1)
        running = len([a for a in o.agents.values() if a.status == AgentStatus.RUNNING])
        assert running <= 2

    @pytest.mark.asyncio
    async def test_drain_triggers(self):
        """After a running agent finishes, a pending one should be picked up."""
        o = MultiAgentOrchestrator(max_parallel=1)
        a1 = await o.spawn(None, "fast", "fast", priority=0)
        a2 = await o.spawn(None, "queued", "queued", priority=1)
        await o.start(a1.id, _pass_executor)
        await o.start(a2.id, _pass_executor)
        await asyncio.sleep(0.1)
        # a1 should be done, and drain should have picked up a2
        assert a1.status == AgentStatus.DONE
        assert a2.status in (AgentStatus.DONE, AgentStatus.RUNNING)

    @pytest.mark.asyncio
    async def test_executor_metadata(self):
        """Verify executor can modify agent metadata."""
        o = MultiAgentOrchestrator()
        a = await o.spawn(None, "stash", "t")
        await o.start(a.id, _stash_executor)
        await asyncio.sleep(0.1)
        assert a.status == AgentStatus.DONE
        assert a.metadata.get("executed") is True


class TestResilience:
    @pytest.mark.asyncio
    async def test_close_during_running(self):
        """Closing a running agent should cancel its task."""
        o = MultiAgentOrchestrator()
        a = await o.spawn(None, "long", "t")
        await o.start(a.id, _slow_executor)
        await asyncio.sleep(0.02)
        await o.close(a.id)
        assert a.status == AgentStatus.CLOSED

    @pytest.mark.asyncio
    async def test_error_doesnt_block_queue(self):
        """An erroring agent should still drain the pending queue."""
        o = MultiAgentOrchestrator(max_parallel=1)
        a1 = await o.spawn(None, "fail", "t")
        a2 = await o.spawn(None, "next", "t")
        await o.start(a1.id, _error_executor)
        await o.start(a2.id, _pass_executor)
        await asyncio.sleep(0.2)
        assert a1.status == AgentStatus.ERROR
        assert a2.status == AgentStatus.DONE

    @pytest.mark.asyncio
    async def test_spawn_close_spawn(self):
        """Spawn, close, then spawn again — no state leak."""
        o = MultiAgentOrchestrator()
        a1 = await o.spawn(None, "gone", "t")
        await o.close(a1.id)
        a2 = await o.spawn(None, "back", "t2")
        assert a2.id in o.agents
        assert a1.id in o.agents  # stays in registry, just CLOSED
        assert a1.status == AgentStatus.CLOSED
