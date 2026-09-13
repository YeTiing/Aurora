# 自研六阶段状态机 — 手写 while 主循环 + SSE 事件集成 + 条件分支（非 LangGraph）

from __future__ import annotations

import asyncio, logging, os, re, time, traceback

from typing import Any, Literal, Callable

from .state import AgentState, AgentStateDict, Message

from .nodes import (

    planner_node, tool_select_node, executor_node,

    observer_node, synthesizer_node, truncate_tool_output

)

from .llm_client import LLMClient

from .checkpoint import CheckpointManager, get_checkpoint_manager

from .sse_events import sse_bus, SSEEventBus

from backend.goal import goal_manager

from backend.context.token_tracker import TokenBudget
import logging
logger = logging.getLogger("aurora")

# ── Necessity 钩子（可选，默认关闭）─────────────────────────────
# 通过环境变量 AURORA_NECESSITY_PATH 指向 necessity 项目的根目录来启用。
# 不硬编码路径：本仓库是公开的，把本地绝对路径写进版本历史既不可移植
# 也会泄漏目录结构；而且 necessity 未发布，对其他人没有意义。
# 未设置该变量时 _nsk_hooks 为 None，所有挂载点直接跳过 ——
# 宿主行为与未挂载时逐字节一致（I1 空操作挂载的验收判据）。
_nsk_hooks = None
_nsk_path = os.environ.get("AURORA_NECESSITY_PATH", "").strip()
if _nsk_path:
    try:
        import sys as _nsk_sys
        if _nsk_path not in _nsk_sys.path:
            _nsk_sys.path.insert(0, _nsk_path)
        from adapter.aurora import hooks as _nsk_hooks
    except Exception as e:
        logger.debug("necessity hooks not loaded: %s", e)
        _nsk_hooks = None



# 沙箱模式别名归一化。
# 项目里存在三套取值：graph.py 只认 read-only / workspace-only；
# config.sandbox_mode 与 ThreadSettings 默认却是 "workspace-write"；
# 而 "danger-full-access" 只在 system_prompt 出现。
# 归一化前，"workspace-write" 无法匹配任何分支 -> 静默降级为无限制，
# 即"配置了更严的沙箱，实际反而完全放开"。
_SANDBOX_ALIASES = {
    "read-only": "read-only",
    "readonly": "read-only",
    "workspace-write": "workspace-only",
    "workspace-only": "workspace-only",
    "workspacewrite": "workspace-only",
    "full-access": "full-access",
    "danger-full-access": "full-access",
    "": "full-access",
}


def _normalize_sandbox_mode(mode: str) -> str:
    return _SANDBOX_ALIASES.get((mode or "").strip().lower(), "full-access")


def _configured_sandbox_mode() -> str:
    """读取 config.sandbox_mode；失败时回落 full-access（保持原有行为）。"""
    try:
        from backend.config import config as _cfg
        return _cfg.sandbox_mode or "full-access"
    except Exception:
        logger.debug("sandbox_mode config lookup failed", exc_info=True)
        return "full-access"


class AgentGraph:

    """自研六阶段状态机流水线（手写 while 主循环，不依赖 LangGraph）"""


    def __init__(

        self,

        llm: LLMClient,

        tool_handler: Callable,

        tools_schema: list[dict],

        max_turns: int = 30,

        max_empty_turns: int = 3,

        workspace: str = ".",

        checkpoint_manager: CheckpointManager | None = None,

        event_bus: SSEEventBus | None = None,

        token_budget: TokenBudget | None = None,

    ):

        self.llm = llm

        self.tool_handler = tool_handler

        self.tools_schema = tools_schema

        self.max_turns = max_turns
        # 可用计划估算动态放宽（见 _run_tool_select），默认等于 max_turns
        self._effective_max_turns = max_turns

        self.max_empty_turns = max_empty_turns

        self.workspace = workspace

        # 默认复用进程级单例：若每个图各自 new 一个，执行期 save_workspace_state
        # 落的快照与 /checkpoint 路由看到的实例就不是同一个，undo 栈跨请求不可见。
        self.checkpoints = checkpoint_manager or get_checkpoint_manager()

        self.events = event_bus or sse_bus

        self.token_budget = token_budget or TokenBudget(24000)
        self._monitor = None
        self._monitor_started = False
        self._pending_tasks: list = []
        self._pending_tasks_refs: list = []  # keep refs to fire-and-forget tasks
        self._cancelled_sessions: set[str] = set()
        self._last_tracked_tokens = 0
        self._hook_registry = None
        self._transcript_index = None
        self._worktree = None
        # Worktree support
        self._worktree_active = False
        try:
            from backend.worktree import worktree_manager
            self._worktree = worktree_manager
        except ImportError:
            self._worktree = None

        # Start cron scheduler
        from backend.cron_scheduler import get_cron

        # Start plugin hot-reload
        try:
            from backend.plugin_hotreload import get_hotreload
            from backend.plugins import plugin_manager
            hr = get_hotreload()
            # Add plugin dirs
            hr.add_dir("plugins")
            if hasattr(plugin_manager, "_plugin_dirs"):
                for d in plugin_manager._plugin_dirs:
                    hr.add_dir(d)
            hr.set_manager(plugin_manager)
            if not hr.is_running:
                self._pending_tasks.append(lambda: hr.start())
        except ImportError:
            pass

        # Initialize background task monitor
        # 必须在下面的调度判断之前赋值：此前初始化被放在判断之后，
        # self._monitor 恒为 None，导致 if 分支成为死代码、monitor.start(60) 永不入队。
        try:
            from backend.task_monitor import get_monitor
            self._monitor = get_monitor()
        except ImportError:
            self._monitor = None

        # Start background services (lazy init)
        if self._monitor and not self._monitor_started:
            try:
                self._monitor_started = True
                self._pending_tasks.append(lambda: self._monitor.start(60))
            except Exception:
                pass


        # Quality gate: skipped at startup (use CLI: python -m backend.quality_gate)

        # Start heartbeat
        try:
            from backend.heartbeat import heartbeat_manager
            heartbeat_manager.configure(interval=300, enabled=True)
        except ImportError:
            pass

        # Initialize transcript index
        try:
            from backend.transcript_index import get_transcript_index
            self._transcript_index = get_transcript_index()
            self._transcript_index.build()
        except ImportError:
            self._transcript_index = None

        # Initialize hook system
        try:
            from backend.hooks_system import get_hook_registry
            self._hook_registry = get_hook_registry()
        except ImportError:
            self._hook_registry = None
        self.cron = get_cron()

    # Shared helpers for run() / run_with_stream()
    @staticmethod
    def _detect_url(user_input: str) -> tuple[bool, str]:
        """Detect if user_input contains a URL; returns (has_url, extracted_url)."""
        import re
        _m = re.search(r"https?://\S+", user_input)
        if _m:
            return True, _m.group(0)
        _m = re.search(r"www\.[a-zA-Z0-9-]+\.[a-z]{2,}", user_input)
        if _m:
            return True, "https://" + _m.group(0)
        _m = re.search(r"[a-zA-Z0-9][-a-zA-Z0-9]*\.(?:com|cn|net|org|io|dev|app)", user_input)
        if _m:
            return True, "https://" + _m.group(0)
        _url_lower = user_input.lower()
        for _kw in ["打开", "访问", "浏览", "去", "上"]:
            if _kw in _url_lower:
                _m = re.search(r"[a-zA-Z0-9][-a-zA-Z0-9]{1,20}", user_input)
                if _m:
                    return True, "https://" + _m.group(0) + ".com"
        return False, ""


    # ══ 核心执行循环 ══

    async def run(self, user_input: str, session_id: str = "", workspace: str = ".", sandbox_mode: str = "full-access", approval_mode: str = "never", model: str = "", history: list[dict] | None = None, agent_role: str = "", reasoning_effort: str = "medium") -> AgentState:

        self._apply_approval_mode(approval_mode)

        ws = workspace or self.workspace

        # Start deferred background tasks
        for task_fn in self._pending_tasks:
            try:
                t = asyncio.create_task(task_fn()); self._pending_tasks_refs.append(t)
            except Exception:
                pass
        self._pending_tasks.clear()

        state = AgentState(session_id=session_id, workspace=ws, agent_role=agent_role, reasoning_effort=reasoning_effort)

        

        # Apply sandbox mode and model override
        # 配置回退：请求未显式指定时用 config.sandbox_mode
        # （此前该配置字段无任何读取点，用户配置被完全忽略）
        state.sandbox_mode = _normalize_sandbox_mode(sandbox_mode or _configured_sandbox_mode())

        # 审批策略存到 state 上随会话传递，避免写全局单例被并发覆盖

        state.approval_mode = approval_mode or "on-request"

        if model:

            self.llm.set_model(model)


        state.add_message(Message.user(user_input))

        # URL Auto-detection: shared helper
        _url_lower = user_input.lower()
        _has_url, _extracted_url = self._detect_url(user_input)
        # Inject browser_use instruction
        if _has_url and "browser_use" not in _url_lower:
            state.add_message(Message.system(
                "SYSTEM OVERRIDE: Visit " + _extracted_url + ". Use browser_use navigate or web_fetch."
                + " using browser_use with method=navigate."
            ))
        # Check cron for due tasks and inject
        cron_fires = self.cron.pop_fires()
        for task in cron_fires:
            state.add_message(Message.system(f"[Cron: {task.name}] {task.prompt}"))


        await self.events.task_started(session_id, user_input[:200])


        # 简单对话绕过agent循环，直接调用LLM

        chat_keywords = ("hello", "hi", "hey", "你好", "嗨", "谢谢", "thank", "what is", "who are", "how are", "explain", "解释", "帮我理解", "聊天", "聊")

        is_simple_chat = (

            len(user_input) < 200 and

            not any(kw in user_input.lower() for kw in ["code", "代码", "fix", "修", "bug", "error", "错", "build", "test", "file", "文件", "write", "写", "create", "创建", "run", "运行", "deploy", "git", "commit", "install", "安装", "config", "配置", "terminal", "终端", "shell", "重构", "refactor", "delete", "删", "rename", "改", "add ", "加", "patch", "diff", "command", "命令", "api", "API", "docker", "database", "数据库", "www", "http", ".com", ".cn", "打开", "访问", "浏览", "网站", "browse", "open", "navigate", "search the web", "search for", "搜", "查"])

            or

            any(user_input.lower().startswith(kw) for kw in chat_keywords)

        )


        if is_simple_chat:

            try:

                # Inject full closed-loop memory into system prompt
                from backend.dual_memory import get_closed_loop
                cl = get_closed_loop()
                mem = cl.system_prompt(user_input)
                soul_text = ""
                try:
                    from pathlib import Path
                    for p in [Path(".aurora") / "SOUL.md", Path("..") / ".aurora" / "SOUL.md"]:
                        if p.exists():
                            soul_text = p.read_text(encoding="utf-8").strip()
                            break
                except Exception: logger.debug('sync_agent failed', exc_info=True)
                if soul_text:
                    MAX_SOUL_CHARS = 8000
                    if len(soul_text) > MAX_SOUL_CHARS:
                        soul_text = soul_text[:MAX_SOUL_CHARS] + "\n\n[... SOUL.md truncated to " + str(MAX_SOUL_CHARS) + " chars ...]"
                sys_prompt = (soul_text + "\n\n" + mem + "\n\nYou CAN browse the web and open websites. Use browser_use for navigation when asked. Answer concisely and naturally.") if soul_text else ("You are Aurora, a helpful AI assistant. You CAN browse websites and search the web.\n\n" + mem + "\n\nAnswer concisely and naturally.")
                # Build messages with conversation history
                messages = [{"role": "system", "content": sys_prompt}]
                if history:
                    for h in history[-8:]:
                        r = h.get("role","user")
                        if r in ("user", "assistant"):
                            messages.append({"role": r, "content": h.get("content","")})
                messages.append({"role": "user", "content": user_input})
                resp = await self.llm.chat(messages, max_tokens=2000)
                state.final_response = resp.content if hasattr(resp, "content") else str(resp)

            except Exception as e:

                state.final_response = f"Error: {str(e)[:200]}"

            await self.events.agent_message(session_id, state.final_response)

            await self.events.task_complete(session_id, state.final_response[:200])
            state.done = True

            # Process conversation through full closed loop
            try:
                cl = get_closed_loop()
                result = cl.process_turn(user_input, state.final_response)
                # Inject nudge into conversation if triggered
                if result.get("nudge"):
                    state.add_message(Message.system(result["nudge"]))
                # Inject skill creation suggestion
                if result.get("suggest_skill"):
                    state.add_message(Message.system(result["suggest_skill"]))
            except Exception:
                pass

            return state


        # Step 0: Inject closed-loop memory into context
        from backend.dual_memory import get_closed_loop
        try:
            cl = get_closed_loop()
            mem_ctx = cl.system_prompt(user_input)
            if mem_ctx:
                state.add_message(Message.system(f"MEMORY CONTEXT:\n{mem_ctx}"))
        except Exception:
            pass

        # Step 1: Planner

        await self.events.agent_reasoning_delta(session_id, "Planning...")

        await self._run_planner(state)

        await self.events.plan_update(session_id, [p.to_dict() for p in state.plan])


        if not state.plan:

            await self._run_synthesizer(state)

            await self.events.task_complete(session_id, state.final_response[:200])
            state.done = True

            # Process conversation through full closed loop
            try:
                cl = get_closed_loop()
                result = cl.process_turn(user_input, state.final_response)
                # Inject nudge into conversation if triggered
                if result.get("nudge"):
                    state.add_message(Message.system(result["nudge"]))
                # Inject skill creation suggestion
                if result.get("suggest_skill"):
                    state.add_message(Message.system(result["suggest_skill"]))
            except Exception:
                pass

            return state


        # Step 2-4 循环

        while not state.done:

            if session_id in self._cancelled_sessions:
                self._cancelled_sessions.discard(session_id)
                state.add_message(Message.system("Session cancelled by user."))
                break

            if state.total_turns >= self._effective_max_turns:

                state.add_message(Message.system(f"Reached max turns ({self.max_turns}). Stopping."))

                break

            if state.empty_turns >= self.max_empty_turns:

                state.add_message(Message.system(f"Auto-stop after {self.max_empty_turns} empty turns."))

                break

            if goal_manager.is_budget_exhausted():

                state.add_message(Message.system("Goal token budget exhausted."))

                break

            # Track actual token usage delta from LLM
            llm_tokens = getattr(self.llm, '_total_tokens', 0)
            delta = llm_tokens - self._last_tracked_tokens
            if delta > 0:
                self.token_budget.consume(delta)
                self._last_tracked_tokens = llm_tokens
            budget_result = {"exhausted": self.token_budget.usage_ratio() >= 1.0}

            # B12：接近预算上限时先用 LLM 摘要压缩旧消息再续跑，而不是直接中止。
            # 放在 exhausted 判断之前：压缩能移除中止的成因，让长任务跑完而非硬停。
            from backend.agent.nodes import maybe_compact_context
            await maybe_compact_context(state, self.llm, max_tokens=self.token_budget.limit())

            if budget_result["exhausted"]:

                state.add_message(Message.system("Session token budget exhausted."))

                break


            self.checkpoints.save(state, f"pre_turn_{state.total_turns}")


            # ToolSelect

            try:

                await self.events.agent_reasoning_delta(session_id, f"Turn {state.total_turns+1}: Selecting tool...")

                await self._run_tool_select(state)

            except Exception as e:

                state.add_message(Message.system(f"ToolSelect error: {str(e)[:200]}"))

                # total_turns 由 tool_select_node 统一递增（它才是真正消耗一轮的地方），
                # 异常路径不再补加，否则一轮会被记成两次。
                state.empty_turns += 1

                continue


            # Executor

            if state.tool_invocations:

                self._checkpoint_for_tools(state)

                for inv in state.tool_invocations:

                    await self.events.tool_call_begin(session_id, inv.name, inv.id)

                try:

                    await self._run_executor(state)

                    for r in state.tool_results:

                        await self.events.tool_call_end(session_id, r.name, r.invocation_id, r.success,

                            r.output[:500] if r.success else (r.error or ""))

                except Exception as e:

                    await self.events.error(session_id, f"Executor: {str(e)[:200]}")


            # Observer

            await self._run_observer(state)


            if state.plan and all(p.status in ("completed", "failed", "skipped") for p in state.plan):

                break


            # 不在循环末尾再加一次：tool_select_node 已为这一轮计过数。
            self.checkpoints.save(state, f"post_turn_{state.total_turns}")

        state.done = True


        # Step 5: Synthesizer

        await self.events.agent_reasoning_delta(session_id, "Synthesizing final response...")

        await self._run_synthesizer(state)

        self.checkpoints.save(state, "final")

        await self.events.task_complete(session_id, state.final_response[:200])

        # Process full turn + auto-record
        from backend.dual_memory import get_closed_loop
        try:
            cl = get_closed_loop()
            result = cl.process_turn(user_input, state.final_response)
            # Inject nudge if triggered
            if result.get("nudge"):
                state.add_message(Message.system(result["nudge"]))
            # Inject skill suggestion
            if result.get("suggest_skill"):
                state.final_response += "\n\n" + result["suggest_skill"]
            # Run Honcho dialectic if needed
            if result.get("dialectic_needed"):
                depth = cl.honcho.depth_for(len(user_input))
                prompt = cl.honcho.warm_prompt() if cl.honcho.peer.traits else cl.honcho.cold_prompt()
                try:
                    resp = await self.llm.chat_simple(
                        user_message=prompt,
                        system_prompt="You are a user modeling system. Return only valid JSON.",
                        max_tokens=500,
                    )
                    text = resp.content if hasattr(resp, "content") else str(resp)
                    import json as _json, re as _re
                    m = _re.search(r'\{.*\}', text, _re.DOTALL)
                    if m:
                        cl.honcho.apply(_json.loads(m.group()))
                except Exception:
                    pass
            # End session: curator + FTS5 index
            summary = state.final_response[:500] if state.final_response else user_input[:200]
            await cl.end_session(session_id, summary, "", 0, self.llm)
        except Exception:
            pass

        return state


    async def run_with_stream(self, user_input: str, session_id: str = "", workspace: str = ".", sandbox_mode: str = "full-access", approval_mode: str = "never", model: str = "", history: list[dict] | None = None, agent_role: str = "", reasoning_effort: str = "medium"):

        """流式执行 — 每步 yield SSE 进度更新"""

        self._apply_approval_mode(approval_mode)

        ws = workspace or self.workspace

        # Start deferred background tasks
        for task_fn in self._pending_tasks:
            try:
                t = asyncio.create_task(task_fn()); self._pending_tasks_refs.append(t)
            except Exception:
                pass
        self._pending_tasks.clear()

        state = AgentState(session_id=session_id, workspace=ws, agent_role=agent_role, reasoning_effort=reasoning_effort)

        

        # Apply sandbox mode and model override
        # 配置回退：请求未显式指定时用 config.sandbox_mode
        # （此前该配置字段无任何读取点，用户配置被完全忽略）
        state.sandbox_mode = _normalize_sandbox_mode(sandbox_mode or _configured_sandbox_mode())

        # 审批策略存到 state 上随会话传递，避免写全局单例被并发覆盖

        state.approval_mode = approval_mode or "on-request"

        if model:

            self.llm.set_model(model)


        from backend.dual_memory import get_closed_loop
        try:
            _mem = get_closed_loop()
        except Exception:
            _mem = None

        state.add_message(Message.user(user_input))

        # Simple chat: bypass agent loop for greetings and basic questions
        chat_keywords = ("hello", "hi", "hey", "你好", "嗨", "谢谢", "thank", "what is", "who are", "how are", "explain", "解释", "帮我理解", "聊天", "聊")
        is_simple_chat = (
            len(user_input) < 200 and
            not any(kw in user_input.lower() for kw in ["code", "代码", "fix", "修", "bug", "error", "错", "build", "test", "file", "文件", "write", "写", "create", "创建", "run", "运行", "deploy", "git", "commit", "install", "安装", "config", "配置", "terminal", "终端", "shell", "重构", "refactor", "delete", "删", "rename", "改", "add ", "加", "patch", "diff", "command", "命令", "api", "API", "docker", "database", "数据库", "www", "http", ".com", ".cn", "打开", "访问", "浏览", "网站", "browse", "open", "navigate", "search the web", "search for", "搜", "查"])
            or
            any(user_input.lower().startswith(kw) for kw in chat_keywords)
        )
        if is_simple_chat:
            try:
                mem = (_mem.system_prompt(user_input) if _mem else "") or ""
                soul_text = ""
                try:
                    from pathlib import Path
                    for p in [Path(".aurora") / "SOUL.md", Path("..") / ".aurora" / "SOUL.md"]:
                        if p.exists():
                            soul_text = p.read_text(encoding="utf-8").strip()
                            break
                except Exception:
                    pass
                if soul_text and len(soul_text) > 8000:
                    soul_text = soul_text[:8000] + "\n\n[... SOUL.md truncated to 8000 chars ...]"
                sys_prompt = (soul_text + "\n\n" + mem + "\n\nYou CAN browse the web. Answer concisely.") if soul_text else ("You are Aurora, a helpful AI assistant.\n\n" + mem + "\n\nAnswer concisely.")
                messages = [{"role": "system", "content": sys_prompt}]
                if history:
                    for h in history[-8:]:
                        r = h.get("role","user")
                        if r in ("user", "assistant"):
                            messages.append({"role": r, "content": h.get("content","")})
                messages.append({"role": "user", "content": user_input})
                resp = await self.llm.chat(messages, max_tokens=2000)
                state.final_response = resp.content if hasattr(resp, "content") else str(resp)
            except Exception as e:
                state.final_response = f"Error: {str(e)[:200]}"
            yield {"type": "codex/event/agent_message", "data": {"content": state.final_response}, "session_id": session_id}
            yield {"type": "codex/event/task_complete", "data": {"result": state.final_response[:200]}, "session_id": session_id}
            yield {"type": "done", "response": state.final_response}
            return

        # URL 自动探测：复用 _detect_url，不再内联重写一份
        # （此前 run_with_stream 把同一套正则抄了一遍，行为已与 run() 分叉）
        _has_url, _extracted_url = self._detect_url(user_input)

        if _has_url:
            state.add_message(Message.system(
                f"Browser request detected: {_extracted_url}. "
                "Use browser_use tool with method='navigate' to open this URL. "
                "Do not tell the user to open it themselves."
            ))

        # Check cron for due tasks and inject
        cron_fires = self.cron.pop_fires()
        for task in cron_fires:
            yield {"type": "codex/event/agent_message", "data": {"content": f"[Cron: {task.name}] {task.prompt}"}, "session_id": session_id}
            state.add_message(Message.system(f"[Cron: {task.name}] {task.prompt}"))


        # Inject closed-loop memory into stream context
        if _mem:
            try:
                mem_ctx = _mem.system_prompt(user_input)
                if mem_ctx:
                    state.add_message(Message.system(f"MEMORY CONTEXT:\n{mem_ctx}"))
            except Exception:
                pass


        yield {"type": "codex/event/task_started", "data": {"task": user_input[:200]}, "session_id": session_id}


        await self.events.task_started(session_id, user_input[:200])


        yield {"type": "codex/event/agent_reasoning", "data": {"status": "Planning..."}, "session_id": session_id}


        await self._run_planner(state)

        plan_data = [p.to_dict() for p in state.plan]

        yield {"type": "codex/event/plan_update", "data": {"plan": plan_data}, "session_id": session_id}

        await self.events.plan_update(session_id, plan_data)


        if not state.plan:

            await self._run_synthesizer(state)

            yield {"type": "codex/event/task_complete", "data": {"result": state.final_response[:200]}, "session_id": session_id}

            yield {"type": "done", "response": state.final_response}

            return


        session_id = state.session_id

        while not state.done:

            if session_id in self._cancelled_sessions:
                self._cancelled_sessions.discard(session_id)
                state.add_message(Message.system("Session cancelled by user."))
                break

            if state.total_turns >= self._effective_max_turns: break

            if state.empty_turns >= self.max_empty_turns: break

            if goal_manager.is_budget_exhausted():

                state.add_message(Message.system("Goal token budget exhausted."))

                break

            # Track actual token usage delta from LLM
            llm_tokens = getattr(self.llm, '_total_tokens', 0)
            delta = llm_tokens - self._last_tracked_tokens
            if delta > 0:
                self.token_budget.consume(delta)
                self._last_tracked_tokens = llm_tokens
            budget_result = {"exhausted": self.token_budget.usage_ratio() >= 1.0}

            # B12：接近预算上限时先用 LLM 摘要压缩旧消息再续跑，而不是直接中止。
            # 放在 exhausted 判断之前：压缩能移除中止的成因，让长任务跑完而非硬停。
            from backend.agent.nodes import maybe_compact_context
            await maybe_compact_context(state, self.llm, max_tokens=self.token_budget.limit())

            if budget_result["exhausted"]:

                state.add_message(Message.system("Session token budget exhausted."))

                break


            step = state.current_plan_step()

            yield {"type": "codex/event/agent_reasoning_delta", "data": {

                "delta": f"Step {state.current_step+1}/{len(state.plan)}: {step.description if step else 'Processing...'}"

            }, "session_id": session_id}


            self.checkpoints.save(state, f"pre_turn_{state.total_turns}")


            try:

                await self._run_tool_select(state)

            except Exception as e:

                # 同 run()：计数归 tool_select_node，异常路径不重复递增
                state.empty_turns += 1

                yield {"type": "codex/event/error", "data": {"error": str(e)[:200]}, "session_id": session_id}

                continue


            if state.tool_invocations:

                self._checkpoint_for_tools(state)

                for inv in state.tool_invocations:

                    yield {"type": "codex/event/exec_command_begin", "data": {

                        "tool": inv.name, "tool_call_id": inv.id

                    }, "session_id": session_id}

                    await self.events.tool_call_begin(session_id, inv.name, inv.id)


                try:

                    await self._run_executor(state)

                    for r in state.tool_results:

                        yield {"type": "codex/event/exec_command_end", "data": {

                            "tool": r.name, "tool_call_id": r.invocation_id,

                            "success": r.success,

                            "output": (r.output[:200] if r.success else r.error)

                        }, "session_id": session_id}

                        await self.events.tool_call_end(session_id, r.name, r.invocation_id, r.success,

                            r.output[:500] if r.success else (r.error or ""))

                except Exception as e:

                    yield {"type": "codex/event/error", "data": {"error": str(e)[:200]}, "session_id": session_id}


            await self._run_observer(state)


            if state.plan and all(p.status in ("completed", "failed", "skipped") for p in state.plan):

                break


            # 同 run()：不在循环末尾重复计数
            self.checkpoints.save(state, f"post_turn_{state.total_turns}")

            # Mid-stream process turn
            if _mem:
                try:
                    _mem.process_turn(user_input, state.final_response if state.final_response else "")
                except Exception:
                    pass

        state.done = True


        yield {"type": "codex/event/agent_reasoning", "data": {"status": "Synthesizing..."}, "session_id": session_id}

        await self._run_synthesizer(state)

        self.checkpoints.save(state, "final")

        final_plan = [p.to_dict() for p in state.plan]

        yield {"type": "codex/event/task_complete", "data": {"result": state.final_response[:200]}, "session_id": session_id}

        yield {"type": "done", "response": state.final_response, "plan": final_plan}

        # Close memory loop
        if _mem:
            try:
                r = _mem.process_turn(user_input, state.final_response)
                summary = state.final_response[:500] if state.final_response else user_input[:200]
                await _mem.end_session(session_id, summary, "", 0)
            except Exception:
                pass


    # ══ 内部方法 ══

    def _apply_approval_mode(self, approval_mode: str):

        """兼容保留：审批策略现随 state 传递，不再写进程级全局单例。

        原实现调用 approval_bridge.manager.set_policy()，而该 manager 是进程级
        单例 —— 并发会话各自 set_policy 会互相覆盖，A 会话的 never 可能盖掉
        B 会话的 untrusted，即安全策略被静默降级。

        现改为：策略存到 state.approval_mode，由 _run_executor 注入 args
        （arguments["_approval_policy"]），工具侧读取后自行判断；
        approval_gate / shell_command 均优先使用该请求级值。

        全局单例的 policy 仅作为无会话上下文调用方（hooks_system 等）的
        进程级默认值，保持 DEFAULT_APPROVAL_POLICY（on-request，偏严格）。
        """

        return


    async def _run_planner(self, state: AgentState):

        await planner_node(state, self.llm)


    async def _sync_plan_in(self, state: AgentState):
        """把 state.plan 同步进 plan_store，供 plan_update 工具读取。

        工具 handler 的签名是 (arguments, workspace)，拿不到 AgentState，
        因此需要一个按 session 索引的中转存储。没有这一步，plan_update
        只能回一段文本，改不到真正的计划。
        """
        try:
            from backend.tools.plan_store import set_plan
            set_plan(state.session_id, [p.to_dict() for p in state.plan])
        except Exception:
            logger.debug("plan sync-in failed", exc_info=True)

    def _sync_plan_out(self, state: AgentState) -> None:
        """把 plan_update 工具写入的改动合并回 state.plan。

        合并而非整体替换：state.plan 是本轮唯一的真相来源，工具只应改动
        其中被显式指定的步骤；保留 PlanStep 实例也避免丢失 started_at 等
        工具不感知的字段。
        """
        try:
            from backend.tools.plan_store import get_plan
            from backend.agent.state import PlanStep

            incoming = get_plan(state.session_id)
            if not incoming:
                return

            by_num = {int(d.get("step", -1)): d for d in incoming}
            for step in state.plan:
                d = by_num.get(int(step.step))
                if not d:
                    continue
                step.status = d.get("status", step.status)
                if d.get("result") is not None:
                    step.result = d.get("result")

            # 工具可插入新步骤（plan_update 的 new_steps）
            known = {int(s.step) for s in state.plan}
            for d in incoming:
                if int(d.get("step", -1)) not in known:
                    state.plan.append(PlanStep.from_dict(d))
        except Exception:
            logger.debug("plan sync-out failed", exc_info=True)

    async def _run_tool_select(self, state: AgentState):

        # 用计划的 estimated_turns 校验轮次上限：计划声称需要更多轮时放宽，
        # 否则复杂任务会被固定的 max_turns 提前截断。上限最多放宽到 2 倍，
        # 避免 LLM 高估导致无限循环。这也是 estimated_turns 的唯一读取点。
        try:
            if state.total_turns == 0 and state.plan:
                needed = state.plan_estimated_turns()
                if needed > self.max_turns:
                    self._effective_max_turns = min(needed, self.max_turns * 2)
        except Exception:
            logger.debug("estimated_turns lookup failed", exc_info=True)

        await self._sync_plan_in(state)

        await tool_select_node(state, self.llm, self.tools_schema)

        self._sync_plan_out(state)


    async def _run_executor(self, state: AgentState):

        sandbox = _normalize_sandbox_mode(getattr(state, "sandbox_mode", "full-access"))

        # Restricted tools in non-full-access mode

        RESTRICTED_TOOLS = {"shell_command", "file_rw", "apply_patch", "git_ops"}


        async def handler(name, args, ws):

            if sandbox == "read-only" and name in RESTRICTED_TOOLS:

                return {"success": False, "output": "", "error": "Sandbox mode: read-only. Tool blocked: " + name}

            if sandbox == "workspace-only" and name in RESTRICTED_TOOLS:

                # 文件类工具由各自 handler 内的 safe_resolve_path 强制工作区边界；
                # shell 注入边界标志，由 shell_command 做轻量逃逸检查。

                if name == "shell_command" and isinstance(args, dict):

                    args = dict(args); args["_workspace_boundary"] = True

                    args["_approval_policy"] = state.approval_mode

            # 审批策略随请求传递：approval_manager 是进程级单例，若由各会话
            # 各自 set_policy，并发下会互相覆盖（A 的 never 可能盖掉 B 的 untrusted）。
            # 这里把本会话的策略注入 args，由工具侧读取，不再依赖全局态。
            if isinstance(args, dict) and "_approval_policy" not in args:

                args = dict(args); args["_approval_policy"] = state.approval_mode

            # Necessity 预检：可拦截 / 改写（默认 allow，未挂载时直接跳过）
            if _nsk_hooks is not None:
                _nsk_d = _nsk_hooks.before_tool(name, args, state.total_turns)
                if _nsk_d.action == "block":
                    return {"success": False, "output": "", "error": _nsk_d.reason}
                if _nsk_d.action == "modify" and _nsk_d.replacement is not None:
                    name, args = _nsk_d.replacement.name, _nsk_d.replacement.arguments

            _nsk_t0 = time.perf_counter()
            _nsk_result = await self.tool_handler(name, args, ws)
            # Necessity 后检 + 轨迹（只观察，不改结果）
            if _nsk_hooks is not None:
                _nsk_hooks.after_tool(
                    name, _nsk_result, state.total_turns,
                    duration_ms=(time.perf_counter() - _nsk_t0) * 1000,
                )
            return _nsk_result

        await executor_node(state, handler, state.workspace)


    async def _run_observer(self, state: AgentState):

        await observer_node(state)


    # 会改动工作区文件、需要可回滚的工具集合
    _FILE_MUTATING_TOOLS = frozenset({"apply_patch", "file_rw"})

    # 从 unified diff 的 ---/+++ 头提取文件名
    _DIFF_FILE_RE = re.compile(r"^(?:---|\+\+\+) [ab]/(.+)$", re.MULTILINE)

    @classmethod
    def _candidate_paths(cls, inv) -> list[str]:
        """从工具调用参数里提取它将要改动的文件路径。

        apply_patch 的目标路径写在 patch 文本内部（--- a/x / +++ b/x），
        不在参数里；不解析 diff 就取不到目标，新建/删除文件也就无法回滚。
        取不到路径时返回空列表，此时 undo 会如实报告"无可恢复内容"，
        而不是假装成功。
        """
        args = getattr(inv, "arguments", None)
        if not isinstance(args, dict):
            return []

        out: list[str] = []
        for key in ("path", "file", "file_path", "filepath", "destination"):
            v = args.get(key)
            if isinstance(v, str) and v:
                out.append(v)

        # apply_patch: 目标路径藏在 diff 头里
        patch_text = args.get("patch")
        if isinstance(patch_text, str) and patch_text:
            for m in cls._DIFF_FILE_RE.finditer(patch_text):
                name = m.group(1).strip()
                if name and name != "/dev/null":
                    out.append(name)

        return out

    def _checkpoint_for_tools(self, state: AgentState) -> bool:
        """本轮若将执行文件变更工具，先记录这些文件的当前内容以便回滚。

        只快照被本次调用涉及的路径（不是整个工作区）—— 全量拷贝在大仓库上
        不可接受。undo 依赖这份快照做真实还原；此前它只存 label 字符串，
        导致 /checkpoint/undo 返回 undone=True 却不还原任何内容。
        返回是否落了快照（供测试断言）。
        """
        targets: list[str] = []
        for inv in state.tool_invocations:
            if inv.name in self._FILE_MUTATING_TOOLS:
                targets.extend(self._candidate_paths(inv))

        if not targets:
            return False

        # 去重，避免同一文件被多次读盘
        seen: set[str] = set()
        unique: list[str] = []
        for t in targets:
            if t not in seen:
                seen.add(t)
                unique.append(t)

        try:
            self.checkpoints.save_workspace_state(
                label=f"pre_tool_{state.session_id}_{state.total_turns}",
                paths=unique,
                workspace=state.workspace,
            )
            return True
        except Exception:
            logger.debug("workspace checkpoint failed", exc_info=True)
            return False


    async def _run_synthesizer(self, state: AgentState):

        await synthesizer_node(state, self.llm)


    async def resume(self, checkpoint_id: str) -> AgentState | None:

        state = self.checkpoints.load(checkpoint_id)

        if not state: return None

        state.done = False; state.empty_turns = 0


        session_id = state.session_id

        while not state.done:

            if session_id in self._cancelled_sessions:
                self._cancelled_sessions.discard(session_id)
                state.add_message(Message.system("Session cancelled by user."))
                break

            if state.total_turns >= self._effective_max_turns: break

            if state.empty_turns >= self.max_empty_turns: break

            if goal_manager.is_budget_exhausted(): break

            # Track actual token usage delta from LLM
            llm_tokens = getattr(self.llm, '_total_tokens', 0)
            delta = llm_tokens - self._last_tracked_tokens
            if delta > 0:
                self.token_budget.consume(delta)
                self._last_tracked_tokens = llm_tokens
            budget_result = {"exhausted": self.token_budget.usage_ratio() >= 1.0}

            # B12：与 run()/run_with_stream() 一致，先尝试压缩再中止
            from backend.agent.nodes import maybe_compact_context
            await maybe_compact_context(state, self.llm, max_tokens=self.token_budget.limit())

            if budget_result["exhausted"]: break


            self.checkpoints.save(state, f"resume_turn_{state.total_turns}")

            try: await self._run_tool_select(state)

            except Exception as e: logger.error(f"Tool select failed in resume: {e}", exc_info=True); state.empty_turns += 1; continue



            if state.tool_invocations:

                self._checkpoint_for_tools(state)

                try: await self._run_executor(state)

                except Exception as executor_error:
                    logger.error(f"Executor crashed in resume: {executor_error}", exc_info=True)
                    state.empty_turns += 1


            await self._run_observer(state)


            if state.plan and all(p.status in ("completed", "failed", "skipped") for p in state.plan):

                break

            # 同 run()：计数归 tool_select_node，这里不再补加


        await self._run_synthesizer(state)

        self.checkpoints.save(state, "resume_final")

        return state


    async def cancel(self, session_id: str):

        # 只置取消标记，不清理检查点：用户取消后最想做的事就是 resume，
        # 若在此处 clear_session，等于把可恢复的快照一并销毁。
        # 需要清理时应走独立的显式接口，而不是在 cancel 里静默删除。
        self._cancelled_sessions.add(session_id)


    def stats(self) -> dict:

        base = {

            "llm": self.llm.stats,

            "checkpoints": self.checkpoints.stats(),

            "max_turns": self.max_turns,

            "tools_count": len(self.tools_schema),

        }

        if self.token_budget:

            base["token_budget"] = {

                "limit": self.token_budget.limit(),

                "used": self.token_budget.used,

                "remaining": self.token_budget.remaining(),

                "ratio": round(self.token_budget.usage_ratio(), 3),

            }

        return base