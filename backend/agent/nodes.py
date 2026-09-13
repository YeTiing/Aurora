# Agent 六步节点实现 — Planner / ToolSelect / Executor / Observer / Synthesizer
from __future__ import annotations
import asyncio, json, time, re, traceback
from typing import Any, Callable
from dataclasses import asdict
from .state import AgentState, Message, PlanStep, ToolInvocation, ToolResult
from .llm_client import LLMClient
from .llm_providers import LLMResponse, StreamChunk
from .system_prompt import get_cli_prompt, get_desktop_prompt, BU, TOOL_GUIDELINES, CORE_IDENTITY
from backend.goal import goal_manager
from backend.context.token_tracker import TokenBudget
from backend.agent.integration_hooks import post_file_edit_hook, post_session_hook, post_edit_security_hook

SYSTEM_PROMPT = get_desktop_prompt()


def _system_prompt_for(state: AgentState) -> str:
    """按会话角色装配 system prompt；无角色时用默认（避免每次重复装配）。"""
    if getattr(state, "agent_role", ""):
        return get_desktop_prompt(agent_role=state.agent_role)
    return SYSTEM_PROMPT


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
        {"role": "system", "content": _system_prompt_for(state)},
        {"role": "user", "content": PLANNER_PROMPT.format(user_input=user_input[:4000])}
    ]

    try:
        resp = await llm.chat(messages, max_tokens=2000, reasoning_effort=state.reasoning_effort)
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
        {"role": "system", "content": _system_prompt_for(state)},
        *[m.to_openai() for m in state.messages[-20:]],
        {"role": "user", "content": tool_prompt},
    ]

    try:
        resp = await llm.chat(messages, tools=tools_schema, max_tokens=4000, reasoning_effort=state.reasoning_effort)
        content = resp.content
        tool_calls = resp.tool_calls

        if hasattr(resp, 'total_tokens'):
            goal_manager.track_tokens(resp.total_tokens)

        if tool_calls:
            # B1: 并行工具调用只能产生「一条」assistant 消息，由它携带全部
            # tool_calls，后接 N 条 tool 结果。此前 add_message 在循环体内，N 个
            # 调用会写入 N 条完全相同的 assistant 消息（每条都带全部 tool_calls），
            # 随后的 tool 结果无法与之一一配对，下一次 LLM 调用会收到非法历史
            # （重复 tool_call_id / 未配对消息）。
            batch = []
            for idx, tc in enumerate(tool_calls):
                function_call = tc.get("function") or {}
                name = function_call.get("name") or tc.get("name") or "unknown"
                raw_arguments = function_call.get("arguments", tc.get("arguments", "{}"))
                if isinstance(raw_arguments, str):
                    arguments = json.loads(raw_arguments or "{}")
                else:
                    arguments = raw_arguments or {}
                # 缺 id 时用 turn+序号保证唯一；旧回退 call_{turn} 会让同一轮 N 个
                # 调用共用同一个 id，导致重复 tool_call_id。
                call_id = tc.get("id") or f"call_{state.total_turns}_{idx}"
                inv = ToolInvocation(
                    id=call_id,
                    name=name,
                    arguments=arguments
                )
                state.tool_invocations.append(inv)
                batch.append(inv)
            state.add_message(Message.assistant(
                content=content or "Calling " + ", ".join(i.name for i in batch),
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
# 只读工具白名单：无副作用、彼此独立，可安全并发执行。
READ_ONLY_TOOLS = frozenset({
    "code_search", "list_files", "web_fetch", "web_search",
    "view_image", "detective", "lsp", "re",
})

# file_rw 读写合一，只有下列操作才算只读；write/delete/move/copy 会改盘，
# 且可能命中同一文件，必须串行。
_READ_ONLY_FILE_OPS = frozenset({"read", "list", "exists", "info"})

# 并发上限：限制同时打开的 I/O / 网络连接数，避免句柄耗尽。
_MAX_CONCURRENT_TOOLS = 4


def _is_read_only_invocation(inv) -> bool:
    """单个调用是否可无副作用地并发执行。"""
    if inv.name in READ_ONLY_TOOLS:
        return True
    if inv.name == "file_rw":
        return str(inv.arguments.get("operation", "read")) in _READ_ONLY_FILE_OPS
    return False


async def _execute_one(inv, tool_handler: Callable, ws) -> ToolResult:
    """执行单个工具调用（含 metrics 埋点）；异常一律转成失败结果而不抛出。"""
    start = time.time()
    try:
        from backend.tools.tool_metrics import get_metrics
        get_metrics().record_start(inv.name, inv.arguments)
    except ImportError:
        pass
    try:
        result = await tool_handler(inv.name, inv.arguments, ws)
        duration = (time.time() - start) * 1000
        try:
            from backend.tools.tool_metrics import get_metrics
            get_metrics().record_end(inv.name, result.get("success", False), result.get("error", ""), len(str(result.get("output", ""))))
        except ImportError:
            pass
        output = str(result.get("output", ""))
        return ToolResult(
            invocation_id=inv.id,
            name=inv.name,
            output=output[:8000],
            success=result.get("success", False),
            error=result.get("error"),
            duration_ms=duration,
            truncated=len(output) > 8000,
        )
    except Exception as e:
        duration = (time.time() - start) * 1000
        return ToolResult(
            invocation_id=inv.id,
            name=inv.name,
            output="",
            success=False,
            error=f"{type(e).__name__}: {str(e)[:500]}",
            duration_ms=duration,
        )


async def _execute_concurrent(invocations, tool_handler: Callable, ws) -> list[ToolResult]:
    """只读调用并发执行。asyncio.gather 按传入顺序返回结果，天然保持与
    state.tool_invocations 一致，消息配对顺序不变。"""
    sem = asyncio.Semaphore(_MAX_CONCURRENT_TOOLS)

    async def _guarded(inv):
        async with sem:
            return await _execute_one(inv, tool_handler, ws)

    return list(await asyncio.gather(*(_guarded(inv) for inv in invocations)))


async def _post_edit_notes(inv, tr: ToolResult, msg: Message) -> None:
    """后处理：LSP 诊断注入 + 安全扫描，对每个成功结果都执行。"""
    if not (tr.success and inv.name in ("apply_patch", "file_rw")):
        return
    try:
        lsp_note = await post_file_edit_hook(inv.name, inv.arguments, {"success": True, "output": tr.output})
        if lsp_note:
            tr.output += lsp_note
            msg.content += lsp_note
    except Exception:
        pass
    try:
        sec_note = await post_edit_security_hook(
            inv.arguments.get("file_path") or inv.arguments.get("path") or ""
        )
        if sec_note:
            tr.output += sec_note
            msg.content += sec_note
    except Exception:
        pass


async def executor_node(
    state: AgentState,
    tool_handler: Callable,
    ws=None,
) -> dict:
    """Node 3: 执行工具调用并收集结果"""
    # 跳过已执行过的调用（resume 重放时 state.tool_results 已存在）
    pending = [
        inv for inv in state.tool_invocations
        if not any(r.invocation_id == inv.id for r in state.tool_results)
    ]
    if not pending:
        return {"tool_results": []}

    # 安全性：只有「整批都是只读」才并发。apply_patch / git_ops / shell_command /
    # code_exec / file_rw 写操作等可能改同一文件或依赖先后顺序，整批退回串行，
    # 避免竞态和不确定性。
    if len(pending) > 1 and all(_is_read_only_invocation(inv) for inv in pending):
        results = await _execute_concurrent(pending, tool_handler, ws)
    else:
        results = []
        for inv in pending:
            results.append(await _execute_one(inv, tool_handler, ws))

    # 与原实现一致：返回快照不含后处理追加的诊断/安全内容
    result_dicts = [asdict(tr) for tr in results]

    for inv, tr in zip(pending, results):
        state.tool_results.append(tr)
        msg = Message.tool(
            content=tr.output or tr.error or "(empty)",
            tool_call_id=inv.id,
            name=inv.name,
        )
        state.add_message(msg)
        await _post_edit_notes(inv, tr, msg)

    return {"tool_results": result_dicts}


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
        {"role": "system", "content": _system_prompt_for(state)},
        *[m.to_openai() for m in state.messages[-15:]],
        {"role": "user", "content": SYNTHESIZER_PROMPT.format(
            user_input=user_input,
            plan_summary=plan_summary,
            results_summary=results_summary,
        )},
    ]

    try:
        resp = await llm.chat(messages, max_tokens=3000, reasoning_effort=state.reasoning_effort)
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
        t = asyncio.create_task(post_session_hook())
        t.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)  # prevent "Task exception was never retrieved"
    except Exception as e:
        import logging
        logging.getLogger("aurora").warning(f"post_session_hook spawn failed: {e}")

    return {"done": True, "final_response": state.final_response}


# ══ 上下文压缩入口 (B12) ══
# LLM 压缩器此前完全未被主循环调用：命中预算时直接 abort，实际只有
# Collapser 的规则截断在跑，"自动摘要压缩"名存实亡。此函数是主循环的
# 单一接线点，graph.py 只需一行：
#     await maybe_compact_context(state, self.llm)
async def maybe_compact_context(
    state: AgentState,
    llm,
    max_tokens: int = 24000,
    threshold: float = 0.85,
) -> bool:
    """上下文超预算时用 LLM 摘要旧消息，并原地替换 state.messages。

    返回是否发生了压缩。压缩器实例挂在 state 上按会话复用，
    避免每轮重建、也保留 compaction 计数。
    """
    try:
        from backend.context.context_manager import ContextManager
    except Exception:
        return False

    cm = getattr(state, "_ctx_manager", None)
    if cm is None:
        cm = ContextManager()
        try:
            state._ctx_manager = cm
        except Exception:
            pass
    cm.set_max_tokens(max_tokens, threshold)
    cm.set_messages([m.to_openai() for m in state.messages])
    if not cm.needs_compaction():
        return False

    try:
        removed = await cm.compact_async(llm)
    except Exception:
        # 压缩失败不应打断主循环
        return False
    if removed <= 0:
        return False

    # 压缩后的 dict 重建为 Message，保留 tool_calls / tool_call_id / name
    # 等配对字段，否则后续 LLM 调用会收到非法历史。
    rebuilt: list[Message] = []
    for m in cm.messages:
        try:
            rebuilt.append(Message(
                role=m.get("role", "user"),
                content=m.get("content", "") or "",
                tool_calls=m.get("tool_calls"),
                tool_call_id=m.get("tool_call_id"),
                name=m.get("name"),
            ))
        except Exception:
            continue
    state.messages = rebuilt
    return True


# ══ 工具 ══
def truncate_tool_output(output: str, max_len: int = 16000) -> str:
    if len(output) <= max_len:
        return output
    head = output[:max_len // 2]
    tail = output[-(max_len // 2):]
    return f"{head}\n\n... [{len(output) - max_len} chars truncated] ...\n\n{tail}"
