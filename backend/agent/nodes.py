# Agent 六步节点实现 — Planner / ToolSelect / Executor / Observer / Synthesizer
from __future__ import annotations
import asyncio, json, time, re, traceback
from typing import Any, Callable
from .state import AgentState, Message, PlanStep, ToolInvocation, ToolResult
from .llm_client import LLMClient
from .llm_providers import LLMResponse, StreamChunk
from .system_prompt import get_cli_prompt, get_desktop_prompt, BU, TOOL_GUIDELINES, CORE_IDENTITY
from backend.goal import goal_manager
from backend.context.token_tracker import TokenBudget
from backend.agent.integration_hooks import post_file_edit_hook, post_session_hook

SYSTEM_PROMPT = get_desktop_prompt()


# ══ Node 1: Planner — 任务拆解 ══
PLANNER_PROMPT = """Analyze the following user request and break it down into a step-by-step execution plan.

Requirements:
- Each step must be concrete and actionable
- Order steps by dependency
- Estimate complexity (1-3 turns per step)
- Return ONLY a JSON array of objects with "step" (int), "description" (string), "tool" (string or null)

User request: {user_input}

Return JSON:"""


async def planner_node(state: AgentState, llm: LLMClient) -> dict:
    """Node 1: 将用户任务拆解为执行计划"""
    user_msgs = [m for m in state.messages if m.role == "user"]
    user_input = user_msgs[-1].content if user_msgs else "No input"

    if state.plan and len(state.plan) > 0:
        return {}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": PLANNER_PROMPT.format(user_input=user_input[:4000])}
    ]

    try:
        resp = await llm.chat(messages, max_tokens=2000)
        content = resp.content
        if hasattr(resp, 'total_tokens'):
            goal_manager.track_tokens(resp.total_tokens)
        plan_data = _parse_plan_json(content)
        plan = [PlanStep(step=i+1, description=p.get("description", f"Step {i+1}"),
                         tool=p.get("tool"), estimated_turns=p.get("estimated_turns", 1))
                for i, p in enumerate(plan_data)]
    except Exception as e:
        plan = [PlanStep(step=1, description=f"Execute: {user_input[:100]}")]

    state.plan = plan
    return {"plan": [p.to_dict() for p in plan]}


def _parse_plan_json(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"```\w*\n?", "", text).strip("` \n")
    try:
        data = json.loads(text)
        if isinstance(data, list): return data
        if isinstance(data, dict):
            for key in ("plan", "steps", "tasks"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            return [data]
    except json.JSONDecodeError:
        pass

    steps = []
    for line in text.split("\n"):
        match = re.match(r'^(?:\d+[.)]\s*|[-*]\s*)(.+)', line.strip())
        if match:
            steps.append({"description": match.group(1).strip()})
    if steps:
        return steps

    return [{"description": text[:200]}]


# ══ Node 2: Tool Select — 工具选择 + LLM调用 ══
TOOL_SELECT_PROMPT = """Based on the current plan and conversation, decide what to do next.

Current plan:
{plan_summary}

Progress: step {current_step}/{total_steps} - {current_status}

You have access to these tools:
{tools_description}

Respond with:
1. If you need to use a tool: a function call with the appropriate arguments
2. If the task is complete: a final response summarizing what was done

Be concise and specific. Use the tools when you need to read files, search code, run commands, or apply patches."""


async def tool_select_node(
    state: AgentState,
    llm: LLMClient,
    tools_schema: list[dict],
) -> dict:
    """Node 2: LLM调用 — 选择并调用工具"""
    state.total_turns += 1
    goal_manager.track_turn()

    if goal_manager.is_budget_exhausted():
        state.add_message(Message.system("Token budget exhausted. Stopping."))
        state.done = True
        state.final_response = "Token budget exhausted for this goal."
        return {"done": True, "budget_exhausted": True}

    plan_summary = json.dumps(
        [{"step": p.step, "desc": p.description, "status": p.status}
         for p in state.plan], ensure_ascii=False, indent=2
    )

    current = state.current_plan_step()
    current_status = current.status if current else "N/A"

    tool_prompt = TOOL_SELECT_PROMPT.format(
        plan_summary=plan_summary,
        current_step=state.current_step,
        total_steps=len(state.plan),
        current_status=current_status,
        tools_description=json.dumps(tools_schema, indent=2),
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[m.to_openai() for m in state.messages[-20:]],
        {"role": "user", "content": tool_prompt},
    ]

    try:
        resp = await llm.chat(messages, tools=tools_schema, max_tokens=4000)
        content = resp.content
        tool_calls = resp.tool_calls

        if hasattr(resp, 'total_tokens'):
            goal_manager.track_tokens(resp.total_tokens)

        if tool_calls:
            for tc in tool_calls:
                inv = ToolInvocation(
                    id=tc.get("id", f"call_{state.total_turns}"),
                    name=tc.get("function", {}).get("name", "unknown"),
                    arguments=json.loads(tc.get("function", {}).get("arguments", "{}"))
                )
                state.tool_invocations.append(inv)
                state.add_message(Message.assistant(
                    content=content or f"Calling {inv.name}",
                    tool_calls=tool_calls,
                ))
            state.empty_turns = 0
        elif content and content.strip():
            state.empty_turns = 0
            state.add_message(Message.assistant(content=content))
            if not state.tool_invocations:
                state.final_response = content
        else:
            state.empty_turns += 1

    except Exception as e:
        state.empty_turns += 1
        state.add_message(Message.system(f"LLM error: {str(e)[:200]}"))

    return {}


# ══ Node 3: Executor — 执行工具 ══
async def executor_node(
    state: AgentState,
    tool_handler: Callable,
    ws=None,
) -> dict:
    """Node 3: 执行工具调用并收集结果"""
    results = []
    for inv in state.tool_invocations:
        if any(r.invocation_id == inv.id for r in state.tool_results):
            continue

        start = time.time()
        # Tool metrics: record start
        try:
            from backend.tools.tool_metrics import get_metrics
            get_metrics().record_start(inv.name, inv.arguments)
        except ImportError:
            pass
        try:
            result = await tool_handler(inv.name, inv.arguments, ws)
            duration = (time.time() - start) * 1000
            # Record metrics
            try:
                from backend.tools.tool_metrics import get_metrics
                get_metrics().record_end(inv.name, result.get("success", False), result.get("error", ""), len(str(result.get("output", ""))))
            except ImportError:
                pass
            tr = ToolResult(
                invocation_id=inv.id,
                name=inv.name,
                output=str(result.get("output", ""))[:8000],
                success=result.get("success", False),
                error=result.get("error"),
                duration_ms=duration,
                truncated=len(str(result.get("output", ""))) > 8000,
            )
        except Exception as e:
            duration = (time.time() - start) * 1000
            tr = ToolResult(
                invocation_id=inv.id,
                name=inv.name,
                output="",
                success=False,
                error=f"{type(e).__name__}: {str(e)[:500]}",
                duration_ms=duration,
            )

        state.tool_results.append(tr)
        state.add_message(Message.tool(
            content=tr.output or tr.error or "(empty)",
            tool_call_id=inv.id,
            name=inv.name,
        ))
        results.append(asdict(tr))

        # Post-edit LSP diagnostic injection
        if tr.success and inv.name in ("apply_patch", "file_rw"):
            try:
                lsp_note = await post_file_edit_hook(inv.name, inv.arguments, {"success": True, "output": tr.output})
                if lsp_note:
                    tr.output += lsp_note
                    state.messages[-1].content += lsp_note
            except Exception:
                pass

    return {"tool_results": results}


# ══ Node 4: Observer — 观察 + 状态判断 ══
async def observer_node(state: AgentState) -> dict:
    """Node 4: 分析工具执行结果，决定是否继续"""
    if state.done:
        return {"done": True}

    if state.plan and state.current_step < len(state.plan):
        step = state.plan[state.current_step]
        if step.status == "in_progress":
            step.complete(
                state.tool_results[-1].output[:200]
                if state.tool_results else "Completed"
            )
        state.current_step += 1
        return {"current_step": state.current_step}

    if not state.tool_invocations and not state.empty_turns:
        return {"done": True}

    return {}


# ══ Node 5: Synthesizer — 最终回复 + Diff ══
SYNTHESIZER_PROMPT = """Synthesize the results of the completed task into a clear, concise final response.

Task: {user_input}
Plan executed: {plan_summary}
Results: {results_summary}

Your response should:
1. Summarize what was done
2. List files changed with paths
3. Explain key decisions made
4. Include any relevant code diffs

Keep it concise. Use Markdown for formatting. Include file paths as clickable links."""


async def synthesizer_node(state: AgentState, llm: LLMClient) -> dict:
    """Node 5: 生成最终回复"""
    user_msgs = [m for m in state.messages if m.role == "user"]
    user_input = user_msgs[-1].content[:2000] if user_msgs else "No input"

    plan_summary = json.dumps(
        [{"step": p.step, "desc": p.description, "status": p.status, "result": p.result}
         for p in state.plan], ensure_ascii=False, indent=2
    )

    results = [{
        "tool": r.name, "success": r.success,
        "output": r.output[:500], "error": r.error
    } for r in state.tool_results[-10:]]
    results_summary = json.dumps(results, ensure_ascii=False, indent=2)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[m.to_openai() for m in state.messages[-15:]],
        {"role": "user", "content": SYNTHESIZER_PROMPT.format(
            user_input=user_input,
            plan_summary=plan_summary,
            results_summary=results_summary,
        )},
    ]

    try:
        resp = await llm.chat(messages, max_tokens=3000)
        content = resp.content
        if hasattr(resp, 'total_tokens'):
            goal_manager.track_tokens(resp.total_tokens)
        state.final_response = content
        state.add_message(Message.assistant(content=content))
    except Exception as e:
        state.final_response = f"Task completed. {len(state.tool_results)} tools executed."
        state.add_message(Message.assistant(content=state.final_response))

    state.done = True

    # AutoDream background memory consolidation (fire-and-forget)
    try:
        asyncio.create_task(post_session_hook())
    except Exception:
        pass

    return {"done": True, "final_response": state.final_response}


# ══ 工具 ══
def truncate_tool_output(output: str, max_len: int = 16000) -> str:
    if len(output) <= max_len:
        return output
    head = output[:max_len // 2]
    tail = output[-(max_len // 2):]
    return f"{head}\n\n... [{len(output) - max_len} chars truncated] ...\n\n{tail}"


from dataclasses import asdict