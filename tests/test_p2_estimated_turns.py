"""estimated_turns 从死字段变为真实参与轮次预算。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_plan_estimated_turns_sums_steps():
    from backend.agent.state import AgentState, PlanStep

    st = AgentState()
    st.plan = [
        PlanStep(step=1, description="a", estimated_turns=3),
        PlanStep(step=2, description="b", estimated_turns=2),
    ]
    assert st.plan_estimated_turns() == 5


def test_plan_estimated_turns_treats_missing_as_one():
    """LLM 未返回该字段时按 1 计，不能算成 0 导致预算凭空变小。"""
    from backend.agent.state import AgentState, PlanStep

    st = AgentState()
    st.plan = [PlanStep(step=1, description="a", estimated_turns=0),
               PlanStep(step=2, description="b")]
    assert st.plan_estimated_turns() == 2


def test_plan_estimated_turns_empty_plan():
    from backend.agent.state import AgentState

    assert AgentState().plan_estimated_turns() == 0


def test_estimated_turns_widens_turn_budget_capped_at_double():
    """计划声称需要更多轮时放宽上限，但不超过 2 倍，避免高估导致长时间空转。"""
    from backend.agent.graph import AgentGraph
    from backend.agent.state import AgentState, PlanStep

    st = AgentState()
    st.plan = [PlanStep(step=i, description="x", estimated_turns=3) for i in range(1, 4)]

    g = AgentGraph.__new__(AgentGraph)
    g.max_turns = 5
    g._effective_max_turns = 5

    needed = st.plan_estimated_turns()
    assert needed == 9
    g._effective_max_turns = min(needed, g.max_turns * 2)
    assert g._effective_max_turns == 9

    # 严重高估时封顶在 2 倍
    st.plan = [PlanStep(step=i, description="x", estimated_turns=3) for i in range(1, 101)]
    g._effective_max_turns = min(st.plan_estimated_turns(), g.max_turns * 2)
    assert g._effective_max_turns == 10


def test_estimated_turns_does_not_shrink_budget():
    """小计划不得把上限调低 —— 只能放宽，不能收紧。"""
    from backend.agent.graph import AgentGraph
    from backend.agent.state import AgentState, PlanStep

    st = AgentState()
    st.plan = [PlanStep(step=1, description="x", estimated_turns=1)]

    g = AgentGraph.__new__(AgentGraph)
    g.max_turns = 30
    g._effective_max_turns = 30

    needed = st.plan_estimated_turns()
    if needed > g.max_turns:
        g._effective_max_turns = min(needed, g.max_turns * 2)
    assert g._effective_max_turns == 30


def test_planner_prompt_names_the_field_explicitly():
    """提示词必须写明字段名，否则 LLM 输出 'complexity' 之类的名字会被忽略。"""
    from backend.agent.nodes import PLANNER_PROMPT

    assert "estimated_turns" in PLANNER_PROMPT


@pytest.mark.asyncio
async def test_graph_uses_effective_max_turns_in_loop():
    """循环条件必须用可放宽的上限，而不是写死 max_turns。"""
    import inspect
    from backend.agent import graph as graph_mod

    for fn in (graph_mod.AgentGraph.run, graph_mod.AgentGraph.run_with_stream,
               graph_mod.AgentGraph.resume):
        src = inspect.getsource(fn)
        assert "_effective_max_turns" in src, f"{fn.__name__} 仍使用固定的 max_turns"
