# LangGraph StateGraph — 真实状态图 + SSE事件集成 + 条件边 + Send 并行分发

from __future__ import annotations

import asyncio, time, traceback

from typing import Any, Literal, Callable

from .state import AgentState, AgentStateDict, Message

from .nodes import (

    planner_node, tool_select_node, executor_node,

    observer_node, synthesizer_node, truncate_tool_output

)

from .llm_client import LLMClient

from .checkpoint import CheckpointManager

from .sse_events import sse_bus, SSEEventBus

from backend.goal import goal_manager

from backend.context.token_tracker import TokenBudget



class AgentGraph:

    """基于 LangGraph StateGraph 的六步流水线"""



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

        self.max_empty_turns = max_empty_turns

        self.workspace = workspace

        self.checkpoints = checkpoint_manager or CheckpointManager()

        self.events = event_bus or sse_bus

        self.token_budget = token_budget or TokenBudget(24000)
        # Worktree support
        self._worktree_active = False
        try:
            from backend.worktree import worktree_manager
            self._worktree = worktree_manager
        except ImportError:
            self._worktree = None

        # Start cron scheduler
        from backend.cron_scheduler import get_cron

        # Quality gate check (non-blocking)
        try:
            from backend.quality_gate import QualityGate, QualityGateConfig
            cfg = QualityGateConfig.from_args(quick=True)
            gate = QualityGate(cfg)
            # Fire-and-forget: gate.check_async()
        except ImportError:
            pass

        # Start heartbeat
        try:
            from backend.heartbeat import heartbeat_manager
            heartbeat_manager.configure(interval=300, enabled=True)
        except ImportError:
            pass
        self.cron = get_cron()



    # ══ 核心执行循环 ══

    async def run(self, user_input: str, session_id: str = "", workspace: str = ".", sandbox_mode: str = "full-access", model: str = "", history: list[dict] | None = None) -> AgentState:

        ws = workspace or self.workspace

        state = AgentState(session_id=session_id, workspace=ws)

        

        # Apply sandbox mode and model override

        state.sandbox_mode = sandbox_mode

        if model:

            self.llm.set_model(model)



        state.add_message(Message.user(user_input))
        # Check cron for due tasks and inject
        cron_fires = self.cron.pop_fires()
        for task in cron_fires:
            state.add_message(Message.system(f"[Cron: {task.name}] {task.prompt}"))




        await self.events.task_started(session_id, user_input[:200])



        # 简单对话绕过agent循环，直接调用LLM

        chat_keywords = ("hello", "hi", "hey", "你好", "嗨", "谢谢", "thank", "what is", "who are", "how are", "explain", "解释", "帮我理解", "聊天", "聊")

        is_simple_chat = (

            len(user_input) < 200 and

            not any(kw in user_input.lower() for kw in ["code", "代码", "fix", "修", "bug", "error", "错", "build", "test", "file", "文件", "write", "写", "create", "创建", "run", "运行", "deploy", "git", "commit", "install", "安装", "config", "配置", "terminal", "终端", "shell", "重构", "refactor", "delete", "删", "rename", "改", "add ", "加", "patch", "diff", "command", "命令", "api", "API", "docker", "database", "数据库"])

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
                except: pass
                sys_prompt = (soul_text + "\n\n" + mem + "\n\nAnswer concisely and naturally.") if soul_text else ("You are Aurora, a helpful AI assistant.\n\n" + mem + "\n\nAnswer concisely and naturally.")
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

            if state.total_turns >= self.max_turns:

                state.add_message(Message.system(f"Reached max turns ({self.max_turns}). Stopping."))

                break

            if state.empty_turns >= self.max_empty_turns:

                state.add_message(Message.system(f"Auto-stop after {self.max_empty_turns} empty turns."))

                break

            if goal_manager.is_budget_exhausted():

                state.add_message(Message.system("Goal token budget exhausted."))

                break

            budget_result = self.token_budget.consume(0)

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

                state.empty_turns += 1; state.total_turns += 1

                continue



            # Executor

            if state.tool_invocations:

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



            state.total_turns += 1

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



    async def run_with_stream(self, user_input: str, session_id: str = "", workspace: str = ".", sandbox_mode: str = "full-access", model: str = "", history: list[dict] | None = None):

        """流式执行 — 每步 yield SSE 进度更新"""

        ws = workspace or self.workspace

        state = AgentState(session_id=session_id, workspace=ws)

        

        # Apply sandbox mode and model override

        state.sandbox_mode = sandbox_mode

        if model:

            self.llm.set_model(model)



        from backend.dual_memory import get_closed_loop
        try:
            _mem = get_closed_loop()
        except Exception:
            _mem = None

        state.add_message(Message.user(user_input))
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



        while not state.done:

            if state.total_turns >= self.max_turns: break

            if state.empty_turns >= self.max_empty_turns: break

            if goal_manager.is_budget_exhausted():

                state.add_message(Message.system("Goal token budget exhausted."))

                break

            budget_result = self.token_budget.consume(0)

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

                state.empty_turns += 1; state.total_turns += 1

                yield {"type": "codex/event/error", "data": {"error": str(e)[:200]}, "session_id": session_id}

                continue



            if state.tool_invocations:

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



            state.total_turns += 1

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

    async def _run_planner(self, state: AgentState):

        await planner_node(state, self.llm)



    async def _run_tool_select(self, state: AgentState):

        await tool_select_node(state, self.llm, self.tools_schema)



    async def _run_executor(self, state: AgentState):

        sandbox = getattr(state, "sandbox_mode", "full-access")

        # Restricted tools in non-full-access mode

        RESTRICTED_TOOLS = {"shell_command", "file_rw", "apply_patch", "git_ops"}



        async def handler(name, args, ws):

            if sandbox == "read-only" and name in RESTRICTED_TOOLS:

                return {"success": False, "output": "", "error": "Sandbox mode: read-only. Tool blocked: " + name}

            if sandbox == "workspace-only" and name in RESTRICTED_TOOLS:

                # Allow only within workspace boundary

                pass  # Full workspace-only enforcement done in tool itself

            return await self.tool_handler(name, args, ws)

        await executor_node(state, handler, state.workspace)



    async def _run_observer(self, state: AgentState):

        await observer_node(state)



    async def _run_synthesizer(self, state: AgentState):

        await synthesizer_node(state, self.llm)



    async def resume(self, checkpoint_id: str) -> AgentState | None:

        state = self.checkpoints.load(checkpoint_id)

        if not state: return None

        state.done = False; state.empty_turns = 0



        while not state.done:

            if state.total_turns >= self.max_turns: break

            if state.empty_turns >= self.max_empty_turns: break

            if goal_manager.is_budget_exhausted(): break

            budget_result = self.token_budget.consume(0)

            if budget_result["exhausted"]: break



            self.checkpoints.save(state, f"resume_turn_{state.total_turns}")

            try: await self._run_tool_select(state)

            except: state.empty_turns += 1; state.total_turns += 1; continue



            if state.tool_invocations:

                try: await self._run_executor(state)

                except: pass



            await self._run_observer(state)



            if state.plan and all(p.status in ("completed", "failed", "skipped") for p in state.plan):

                break

            state.total_turns += 1



        await self._run_synthesizer(state)

        self.checkpoints.save(state, "resume_final")

        return state



    async def cancel(self, session_id: str):

        self.checkpoints.clear_session(session_id)



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