"""P1 生命周期修复的回归测试。

覆盖 A1（会话级图隔离）、B2（total_turns 单次计数）、B3（monitor 初始化顺序）、
B4（checkpoint 单例 + 文件工具前置快照）、B5（resume 路由）、B6（cancel 保留检查点）。
每个用例都断言真实行为，不做空跑冒烟。
"""
from __future__ import annotations

import pytest

from backend.agent.checkpoint import CheckpointManager
from backend.agent.graph import AgentGraph
from backend.agent.llm_client import MockLLMClient
from backend.agent.state import AgentState, Message, PlanStep, ToolInvocation
from backend.api import deps
from backend.api.routes import sessions as sessions_routes
from backend.api.routes.sessions import ResumeRequest


async def _noop_tool_handler(name, args, ws):
    return {"success": True, "output": f"mock {name}"}


def _make_graph(**overrides):
    kwargs = dict(
        llm=MockLLMClient(),
        tool_handler=_noop_tool_handler,
        tools_schema=[],
        max_turns=30,
        max_empty_turns=3,
        workspace=".",
    )
    kwargs.update(overrides)
    return AgentGraph(**kwargs)


def _long_plan_input() -> str:
    """超过 200 字符且含 "plan"，使 planner 返回 4 步计划并绕过简易聊天分支。"""
    return "plan " + "x" * 260


# ══ A1: 会话级 AgentGraph 隔离 ══

def test_get_graph_for_isolates_per_session_state(monkeypatch):
    # 用 Mock LLM 避开 API Key / 网络依赖
    monkeypatch.setattr(deps, "_build_llm", lambda: MockLLMClient())
    deps.reset_deps()

    g1 = deps.get_graph_for("session-a")
    g2 = deps.get_graph_for("session-b")

    # 同一会话复用同一实例，不同会话必须是不同实例
    assert deps.get_graph_for("session-a") is g1
    assert g1 is not g2

    # 预算、token 记账、LLM 客户端三者都不能共享
    assert g1.token_budget is not g2.token_budget
    assert g1.llm is not g2.llm

    # 一个会话的记账/预算耗尽不得影响另一个会话
    g1._last_tracked_tokens = 12345
    assert g2._last_tracked_tokens == 0

    g1.token_budget.consume(10**9)
    assert g1.token_budget.usage_ratio() >= 1.0
    assert g2.token_budget.used == 0
    assert g2.token_budget.usage_ratio() < 1.0

    # model 覆盖只作用于本会话的 client
    g1.llm.set_model("model-a")
    g2.llm.set_model("model-b")
    assert g1.llm.config.model == "model-a"
    assert g2.llm.config.model == "model-b"

    deps.reset_deps()


def test_get_graph_backwards_compatible_delegates_to_default(monkeypatch):
    monkeypatch.setattr(deps, "_build_llm", lambda: MockLLMClient())
    deps.reset_deps()

    legacy = deps.get_graph()
    assert legacy is deps.get_graph()
    # 旧的 graph() 仍可用，并与默认会话图一致
    assert legacy is deps.get_graph_for(deps._DEFAULT_SESSION)

    deps.reset_deps()


def test_session_registry_evicts_old_entries(monkeypatch):
    monkeypatch.setattr(deps, "_build_llm", lambda: MockLLMClient())
    monkeypatch.setattr(deps, "_MAX_SESSION_GRAPHS", 2)
    deps.reset_deps()

    deps.get_graph_for("s-1")
    deps.get_graph_for("s-2")
    deps.get_graph_for("s-3")  # 触发 LRU 淘汰

    with deps._session_graphs_lock:
        assert list(deps._session_graphs.keys()) == ["s-2", "s-3"]

    deps.reset_deps()


# ══ B2: total_turns 每轮恰好 +1 ══

@pytest.mark.asyncio
async def test_total_turns_increments_once_per_iteration_happy_path(monkeypatch):
    import backend.agent.graph as graph_mod

    real_node = graph_mod.tool_select_node
    calls = {"n": 0}

    async def counting_node(state, llm, tools_schema):
        calls["n"] += 1
        return await real_node(state, llm, tools_schema)

    monkeypatch.setattr(graph_mod, "tool_select_node", counting_node)

    # 计划永不完结（MockLLM 不推进步骤状态），循环将在 max_turns 处停下；
    # 真正跑了的轮数必须与 total_turns 完全一致。
    g = _make_graph(max_turns=5)
    state = await g.run(_long_plan_input(), session_id="turns-happy")

    assert calls["n"] == 5
    # 修复前节点与循环末尾各加一次，5 次调用会记成 10（且只跑 3 轮就撞上限）
    assert state.total_turns == calls["n"] == 5, (
        f"calls={calls['n']} total_turns={state.total_turns}"
    )


@pytest.mark.asyncio
async def test_total_turns_increments_once_per_iteration_on_exception(monkeypatch):
    import backend.agent.graph as graph_mod

    calls = {"n": 0}

    async def boom(state, llm, tools_schema):
        # 模拟 tool_select_node 语义：真正消耗一轮的地方先计数，再出错
        calls["n"] += 1
        state.total_turns += 1
        raise RuntimeError("tool select boom")

    monkeypatch.setattr(graph_mod, "tool_select_node", boom)

    # 计划永不完成；连续 3 次异常触发 empty_turns 上限后退出
    g = _make_graph(max_empty_turns=3)
    state = await g.run(_long_plan_input(), session_id="turns-exc")

    assert state.empty_turns == 3
    # 异常路径若仍补加一次，这里 total_turns 会是 6
    assert state.total_turns == calls["n"] == 3, (
        f"calls={calls['n']} total_turns={state.total_turns}"
    )


# ══ B3: monitor 初始化顺序 ══

def test_monitor_is_initialized_before_scheduling(monkeypatch):
    import backend.task_monitor as task_monitor

    class FakeMonitor:
        def __init__(self):
            self.started_with = None

        async def start(self, interval):
            self.started_with = interval

    fake = FakeMonitor()
    monkeypatch.setattr(task_monitor, "get_monitor", lambda: fake)

    g = _make_graph()

    assert g._monitor is fake
    # 修复前 _monitor_started 恒为 False（判断先于初始化，分支是死代码）
    assert g._monitor_started is True
    assert g._pending_tasks, "monitor.start 应被排入待启动任务"


# ══ B4: checkpoint 单例 + 文件工具前置快照 ══

def test_graph_pushes_workspace_snapshot_before_file_tool(tmp_path):
    mgr = CheckpointManager(storage_dir=str(tmp_path))
    g = _make_graph(checkpoint_manager=mgr)

    state = AgentState(session_id="ws-1")
    assert g._checkpoint_for_tools(state) is False  # 无文件工具，不落快照

    state.tool_invocations.append(ToolInvocation(id="c1", name="apply_patch", arguments={}))
    assert g._checkpoint_for_tools(state) is True
    assert len(mgr.list_history()) == 1
    assert mgr.undo() is not None  # 栈里确实有内容


@pytest.mark.asyncio
async def test_checkpoint_routes_use_persistent_manager(tmp_path, monkeypatch):
    import backend.agent.checkpoint as checkpoint_mod

    mgr = CheckpointManager(storage_dir=str(tmp_path))
    # 路由与图共用同一单例；若路由仍 new 实例，下面会拿到 "Nothing to undo"
    monkeypatch.setattr(checkpoint_mod, "_checkpoint_manager", mgr)
    mgr.save_workspace_state("pre_tool_apply_patch")

    listed = await sessions_routes.list_checkpoints()
    assert listed["count"] == 1
    assert listed["undo_count"] == 1

    undone = await sessions_routes.undo_checkpoint()
    assert undone["undone"] is True
    assert undone["checkpoint_id"]

    redone = await sessions_routes.redo_checkpoint()
    assert redone["redone"] is True


# ══ B5: resume 路由 ══

@pytest.mark.asyncio
async def test_resume_route_continues_from_checkpoint(tmp_path, monkeypatch):
    import backend.agent.checkpoint as checkpoint_mod

    monkeypatch.setattr(deps, "_build_llm", lambda: MockLLMClient())
    deps.reset_deps()

    mgr = CheckpointManager(storage_dir=str(tmp_path))
    monkeypatch.setattr(checkpoint_mod, "_checkpoint_manager", mgr)

    state = AgentState(session_id="resume-s1", plan=[PlanStep(step=1, description="do it")])
    state.add_message(Message.user(_long_plan_input()))
    cid = mgr.save(state, "pre_resume")

    result = await sessions_routes.resume_checkpoint(ResumeRequest(checkpoint_id=cid))

    assert result["session_id"] == "resume-s1"
    assert isinstance(result["final_response"], str)
    assert result["plan"]

    deps.reset_deps()


@pytest.mark.asyncio
async def test_resume_route_404_for_unknown_checkpoint(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import backend.agent.checkpoint as checkpoint_mod

    mgr = CheckpointManager(storage_dir=str(tmp_path))
    monkeypatch.setattr(checkpoint_mod, "_checkpoint_manager", mgr)

    with pytest.raises(HTTPException) as exc:
        await sessions_routes.resume_checkpoint(ResumeRequest(checkpoint_id="nope"))
    assert exc.value.status_code == 404


# ══ B6: cancel 保留检查点 ══

@pytest.mark.asyncio
async def test_cancel_preserves_checkpoints(tmp_path):
    mgr = CheckpointManager(storage_dir=str(tmp_path))
    g = _make_graph(checkpoint_manager=mgr)

    state = AgentState(session_id="cancel-s1")
    state.add_message(Message.user("hello"))
    cid = mgr.save(state, "before_cancel")
    assert mgr.load(cid) is not None

    await g.cancel("cancel-s1")

    # 取消只是打断循环，快照必须保留供 resume 使用
    assert "cancel-s1" in g._cancelled_sessions
    assert mgr.load(cid) is not None
    assert mgr.get_latest("cancel-s1") is not None
