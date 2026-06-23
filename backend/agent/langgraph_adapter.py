# Aurora LangGraph 适配层 — 可选替换自研 AgentGraph
"""当 langgraph 可用时，可以切换到此实现。否则使用自研 graph.py"""
from __future__ import annotations
import json, time
from typing import Any, Literal
from .state import AgentState, AgentStateDict, Message
from .nodes import planner_node, tool_select_node, executor_node, observer_node, synthesizer_node

# 尝试导入 LangGraph
_HAS_LANGGRAPH = False
try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    _HAS_LANGGRAPH = True
except ImportError:
    pass


def build_langgraph_pipeline(
    llm_client,
    tool_handler,
    tools_schema: list[dict],
    max_turns: int = 30,
) -> "StateGraph":
    """构建 LangGraph StateGraph 六步流水线"""
    if not _HAS_LANGGRAPH:
        raise ImportError("langgraph not installed. pip install langgraph")

    workflow = StateGraph(AgentStateDict)

    # 注册节点
    async def planner(state_dict: AgentStateDict) -> AgentStateDict:
        state = AgentState.from_dict(state_dict)
        await planner_node(state, llm_client)
        return state.to_dict()

    async def tool_select(state_dict: AgentStateDict) -> AgentStateDict:
        state = AgentState.from_dict(state_dict)
        await tool_select_node(state, llm_client, tools_schema)
        return state.to_dict()

    async def executor(state_dict: AgentStateDict) -> AgentStateDict:
        state = AgentState.from_dict(state_dict)
        async def handler(name, args, ws):
            return await tool_handler(name, args, state.workspace)
        await executor_node(state, handler, state.workspace)
        return state.to_dict()

    async def observer(state_dict: AgentStateDict) -> AgentStateDict:
        state = AgentState.from_dict(state_dict)
        await observer_node(state, llm_client)
        return state.to_dict()

    async def synthesizer(state_dict: AgentStateDict) -> AgentStateDict:
        state = AgentState.from_dict(state_dict)
        await synthesizer_node(state, llm_client)
        return state.to_dict()

    workflow.add_node("planner", planner)
    workflow.add_node("tool_select", tool_select)
    workflow.add_node("executor", executor)
    workflow.add_node("observer", observer)
    workflow.add_node("synthesizer", synthesizer)

    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "tool_select")
    workflow.add_edge("tool_select", "executor")
    workflow.add_edge("executor", "observer")

    # 条件边: observer → tool_select (继续) 或 synthesizer (完成)
    def should_continue(state_dict: AgentStateDict) -> Literal["tool_select", "synthesizer"]:
        state = AgentState.from_dict(state_dict)
        if state.done or state.total_turns >= max_turns:
            return "synthesizer"
        state.total_turns += 1
        return "tool_select"

    workflow.add_conditional_edges("observer", should_continue, {
        "tool_select": "tool_select",
        "synthesizer": "synthesizer",
    })
    workflow.add_edge("synthesizer", END)

    return workflow.compile(checkpointer=MemorySaver())


def check_langgraph_available() -> bool:
    return _HAS_LANGGRAPH