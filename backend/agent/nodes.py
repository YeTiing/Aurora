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

import logging

logger = logging.getLogger("aurora.agent.nodes")

SYSTEM_PROMPT = get_desktop_prompt()


def _tools_digest(tools_schema: list[dict] | None, max_chars: int = 3000) -> str:
    """把工具表压成**一行一个**的简报，用于提示词。

    为什么必须压缩：工具 schema 已经通过原生 `tools=` 参数传给模型了，
    再把它整份 `json.dumps(indent=2)` 塞进 prompt 是**重复**的。
    实测 29 个工具 = 35,130 字符 ≈ 11,710 tokens，而每次 tool_select 的
    prompt 合计约 14,584 tokens、会话预算是 24,000 —— 于是 Agent 只有
    约 1 个可用轮次，第 2 轮就「预算耗尽」收工，任务必然做不完。
    而模型在原生 schema 之外还看到一份 JSON 文本，也更容易改成输出文本。

    这里只给「名字: 首行描述」，够模型挑工具；参数细节由原生 schema 提供。
    """
    lines: list[str] = []
    for t in (tools_schema or []):
        fn = t.get("function") if isinstance(t, dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "")
        if not name:
            continue
        desc = str(fn.get("description") or "").strip().split("\n")[0][:100]
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        # 截断要**说明**，否则模型以为工具就这些
        out = out[:max_chars] + f"\n...（另有 {len(lines)} 个工具，完整定义见原生工具参数）"
    return out


def _with_type(tool_calls: list[dict] | None) -> list[dict]:
    """补齐 tool_calls 每项的 `type: "function"`（OpenAI 兼容接口的必需字段）。

    为什么需要单独一个函数：写入历史的 tool_calls 是从 provider 响应
    **逐项重建**出来的（只保留 id/name/arguments），丢掉了 `type`。
    而请求体校验要求它存在，否则下一次调用返回 400：
        messages[N]: missing field `type`
    这在只调一次 LLM 的场景下不会暴露 —— 只有「先调工具、再把历史发回去」
    时才触发，也就是**恰好每次 Agent 循环**。

    幂等：已有 type 的项原样保留（provider 原样返回时不该被改写）。
    """
    out = []
    for tc in (tool_calls or []):
        if not isinstance(tc, dict):
            continue
        if "type" in tc:
            out.append(tc)
            continue
        # 兼容两种形状：{"id","function":{...}} 与扁平的 {"id","name","arguments"}
        if "function" in tc:
            out.append({"type": "function", **tc})
        else:
            out.append({
                "id": tc.get("id", ""),
                "type": "function",
                "function": {"name": tc.get("name", ""),
                             "arguments": tc.get("arguments", "{}")},
            })
    return out


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
- Estimate complexity as "estimated_turns" (int, 1-3 turns per step)
- Return ONLY a JSON array of objects with "step" (int), "description" (string), "tool" (string or null), "estimated_turns" (int)

Do NOT call tools in this step. Do NOT attempt to read files, run commands, or search the codebase.
You have no tool access here — your ONLY job is to output the plan as JSON.

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
        # 解析失败时把原始输出记下来 —— 上面的 except 会把它替换成一句
        # 「Execute: ...」，原始内容就再也看不到了，而它正是定位问题的唯一线索。
        if not _looks_like_plan(plan_data):
            logger.warning("planner 未产出 JSON 计划，回退为单步。原始输出前 500 字: %s",
                           (content or "")[:500])
        plan = [PlanStep(step=i+1, description=p.get("description", f"Step {i+1}"),
                         tool=p.get("tool"), estimated_turns=p.get("estimated_turns", 1))
                for i, p in enumerate(plan_data)]
    except Exception as e:
        plan = [PlanStep(step=1, description=f"Execute: {user_input[:100]}")]

    state.plan = plan
    return {"plan": [p.to_dict() for p in plan]}


# 模型把「工具调用」编成文本时的标记。出现这些说明它把 planner 当成了
# 要动手干活的环节 —— 那整段文本**不能**当作一个计划步骤（实测踩过：
# 计划变成单步 '<invoke name="shell_command">...'，随后整条循环崩坏）。
_TOOL_MARKUP_RE = re.compile(r"<\s*(invoke|tool_calls?|antml:invoke|function_calls)\b", re.I)


def _looks_like_plan(plan_data: Any) -> bool:
    """判断解析结果是不是**真计划**（而不是一段工具调用文本）。"""
    if not isinstance(plan_data, list) or not plan_data:
        return False
    first = plan_data[0]
    if not isinstance(first, dict):
        return False
    desc = str(first.get("description") or "")
    if _TOOL_MARKUP_RE.search(desc):
        return False
    # 真计划至少有一项带描述；纯文本回退项也算不合格
    return bool(desc.strip())


def _parse_plan_json(text: str) -> list[dict]:
    text = (text or "").strip()
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

    # JSON 解析失败：模型多半输出了一段散文或工具标记。这里**不再**
    # 把整段文本塞进一个步骤的描述里 —— 那会让计划看起来「有 1 步」，
    # 掩盖「规划失败」这个事实，并让下游拿着一段工具标记当计划执行。
    if _TOOL_MARKUP_RE.search(text):
        return []

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
        # 只给简报，完整 schema 由原生 tools= 提供（见 _tools_digest）
        tools_description=_tools_digest(tools_schema),
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
                # ⚠️ 必须补 `type: "function"`。provider 返回的 tool_calls 里
                # 本来就带这个字段，但 `Message.assistant` 存的是从 provider
                # 响应里**逐项重建**过的列表（只留了 id/name/arguments），于是
                # 再序列化回请求体时缺 `type` —— 下一次调用直接 400：
                #   "Failed to deserialize the JSON body into the target type:
                #    messages[N]: missing field `type`"
                # 后果是**灾难性的且难定位**：第一次工具调用之后的所有 LLM
                # 调用全部失败 →
                #   planner 落到 except（把原始文本当计划）
                #   tool_select 落到 except（只塞一条 system 消息，丢掉 assistant 消息）
                #   模型在畸形历史下退化成输出 XML 文本而不是 native tool_calls
                #   → Agent 全程不写文件，却报 "Task completed."
                tool_calls=_with_type(tool_calls),
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
        # 统一走 truncate_tool_output：此前该函数被 import 却从未调用（死代码），
        # 实际生效的是这里硬编码的 output[:8000] —— 头尾都保留的截断策略从未被使用。
        return ToolResult(
            invocation_id=inv.id,
            name=inv.name,
            output=truncate_tool_output(output, max_len=8000),
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
    """Node 4: 分析工具执行结果，决定是否继续。

    修复：此前只有 status == "in_progress" 的步骤才会被标记完成，而全项目
    没有任何地方调用 PlanStep.start()（唯一把状态置为 in_progress 的方法），
    于是没有步骤会进入 completed，主循环的退出条件
    all(status in completed/failed/skipped) 永不成立，只能靠 max_turns 兜底。

    现在的流转：本轮若有工具执行结果，就把当前步骤推进为 in_progress 并立即
    标记完成（一个步骤对应一轮执行）；工具显式失败时标记 failed。显式失败
    而不是一律算完成，是为了让退出条件能真实反映进度。
    """
    if state.done:
        return {"done": True}

    if state.plan and state.current_step < len(state.plan):
        step = state.plan[state.current_step]
        if step.status in ("pending", "in_progress"):
            if state.tool_results:
                last = state.tool_results[-1]
                if last.success:
                    if step.status != "in_progress":
                        step.start()
                    step.complete(last.output[:200])
                else:
                    step.fail((last.error or "tool failed")[:200])
            # 本轮没有任何工具结果（例如纯文本回复），保持 pending 交给下一轮，
            # 避免把没做过的步骤误标为完成。
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

    # Necessity: 压缩前快照文件状态表 —— 状态表**不得被压缩影响**。
    # 这是 Context Paging 的核心主张：压缩吃掉文件内容后，
    # 状态表仍要记住「读过什么、是否新鲜」，否则 Agent 只能重读。
    try:
        from backend.necessity import mount as _nsk
        _nsk.before_compaction(list(cm.messages))
    except Exception:
        pass

    try:
        removed = await cm.compact_async(llm)
    except Exception:
        # 压缩失败不应打断主循环
        return False
    if removed <= 0:
        return False

    # Necessity: 压缩后注入 file_state 索引（返回增强后的 summary）
    try:
        from backend.necessity import mount as _nsk
        for _m in cm.messages:
            if _m.get("role") == "system":
                _m["content"] = _nsk.after_compaction(_m.get("content", "") or "")
    except Exception:
        pass

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
