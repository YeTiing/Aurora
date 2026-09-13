# 多 Agent 工具 — spawn_agent / send_agent_message / wait_agents / close_agent
# 修复: 之前 MultiAgentOrchestrator 只有 REST 路由可达，LLM 在任务中无法拆解子任务。
# 现在注册为 Agent 工具，子 Agent 用独立 AgentGraph 实例真实执行。
from __future__ import annotations
import json, time, logging
from typing import Any

from .base import ToolSpec, ToolCallResult

logger = logging.getLogger("aurora")

SPAWN_AGENT_SPEC = ToolSpec(
    name="spawn_agent",
    description=(
        "Spawn a sub-agent to work on a subtask in parallel. Returns an agent_id. "
        "After spawning, call wait_agents to collect results. Optionally pass a role "
        "(architect, code-explorer, security-reviewer, refactor-cleaner, typescript-reviewer, "
        "database-reviewer, java-reviewer, java-build-resolver, build-error-resolver, "
        "silent-failure-hunter, explorer, worker, default) to change the sub-agent's system prompt."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short name for the sub-agent"},
            "task": {"type": "string", "description": "The concrete subtask for the sub-agent to complete"},
            "role": {"type": "string", "description": "Optional agent role key (see roles/)"},
            "priority": {"type": "integer", "description": "Priority (higher runs first), default 0"},
        },
        "required": ["task"],
    },
    category="multi_agent",
    timeout_ms=120000,
)

SEND_AGENT_MESSAGE_SPEC = ToolSpec(
    name="send_agent_message",
    description="Send a message/instruction to a running sub-agent.",
    parameters={
        "type": "object",
        "properties": {
            "agent_id": {"type": "string", "description": "Target sub-agent id"},
            "message": {"type": "string", "description": "Message content"},
        },
        "required": ["agent_id", "message"],
    },
    category="multi_agent",
    timeout_ms=15000,
)

WAIT_AGENTS_SPEC = ToolSpec(
    name="wait_agents",
    description=(
        "Wait for one or more sub-agents to finish and return their results. "
        "Use after spawn_agent to collect parallel results."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent_ids": {
                "type": "array", "items": {"type": "string"},
                "description": "List of agent ids to wait for",
            },
            "timeout": {"type": "integer", "description": "Wait timeout in seconds (default 300)"},
        },
        "required": ["agent_ids"],
    },
    category="multi_agent",
    timeout_ms=320000,
)

CLOSE_AGENT_SPEC = ToolSpec(
    name="close_agent",
    description="Close/cleanup a sub-agent (cascade to children).",
    parameters={
        "type": "object",
        "properties": {"agent_id": {"type": "string"}},
        "required": ["agent_id"],
    },
    category="multi_agent",
    timeout_ms=15000,
)


def _agent_summary(node) -> dict:
    return {
        "agent_id": node.id,
        "name": node.name,
        "status": node.status.value,
        "task": node.task[:300],
        "result": (node.result or "")[:2000],
        "error": node.error[:500] if node.error else "",
        "created_at": node.created_at,
    }


async def spawn_agent_handler(arguments: dict, workspace: str = ".") -> dict:
    task = str(arguments.get("task", "")).strip()
    if not task:
        return {"success": False, "error": "task is required"}
    name = str(arguments.get("name", "subagent"))
    role = str(arguments.get("role", ""))
    priority = int(arguments.get("priority", 0))

    from backend.multi_agent import orchestrator
    agent = await orchestrator.spawn(parent_id=None, name=name, task=task, priority=priority,
                                     metadata={"role": role, "workspace": workspace})

    async def executor(node):
        try:
            from backend.agent.graph import AgentGraph
            from backend.api import deps
            from backend.tools import tool_registry
            llm = deps.get_llm()

            async def handler(tname, targs, ws=None):
                result = await tool_registry.execute(tname, targs, ws)
                return {"success": result.success, "output": result.output, "error": result.error}

            sub_graph = AgentGraph(
                llm=llm,
                tool_handler=handler,
                tools_schema=tool_registry.list_tools_openai(),
                max_turns=10,
                workspace=workspace,
            )
            state = await sub_graph.run(
                node.task,
                session_id=node.id,
                workspace=workspace,
                approval_mode="never",
                agent_role=node.metadata.get("role", ""),
            )
            return state.final_response or "No final response."
        except Exception as e:
            logger.error(f"sub-agent {node.id} execution failed: {e}", exc_info=True)
            raise

    await orchestrator.start(agent.id, executor)
    return {"success": True, "agent_id": agent.id, "name": name,
            "status": agent.status.value, "note": "use wait_agents to collect the result"}


async def send_agent_message_handler(arguments: dict, workspace: str = ".") -> dict:
    from backend.multi_agent import orchestrator
    ok = await orchestrator.send(arguments.get("agent_id", ""), arguments.get("message", ""))
    return {"success": ok}


async def wait_agents_handler(arguments: dict, workspace: str = ".") -> dict:
    from backend.multi_agent import orchestrator
    agent_ids = arguments.get("agent_ids") or []
    timeout = int(arguments.get("timeout", 300))
    nodes = await orchestrator.wait(agent_ids, timeout=timeout)
    results = [_agent_summary(n) for n in nodes]
    all_done = all(n.status.value in ("done", "error", "closed") for n in nodes)
    return {"success": all_done, "agents": results}


async def close_agent_handler(arguments: dict, workspace: str = ".") -> dict:
    from backend.multi_agent import orchestrator
    ok = await orchestrator.close(arguments.get("agent_id", ""), cascade=True)
    return {"success": ok}
