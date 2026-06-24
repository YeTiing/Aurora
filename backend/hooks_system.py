# -*- coding: utf-8 -*-
"""Post-Sampling Hooks — pluggable pipeline after model output, before/after tool execution.

Port of cc-haha's hooks system.
Pluggable hooks that run at specific points in the agent lifecycle:
  - post_model_output: after LLM returns, before tool execution
  - pre_tool_exec: before each tool invocation
  - post_tool_exec: after each tool invocation
  - pre_session_end: before session is finalized
  - post_session_end: after session is finalized (for side effects)

Hooks can be registered via config, plugin, or programmatically.
"""

from __future__ import annotations
import asyncio, logging, time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("aurora.hooks")


class HookPoint(str, Enum):
    POST_MODEL_OUTPUT = "post_model_output"
    PRE_TOOL_EXEC = "pre_tool_exec"
    POST_TOOL_EXEC = "post_tool_exec"
    PRE_SESSION_END = "pre_session_end"
    POST_SESSION_END = "post_session_end"


@dataclass
class HookContext:
    hook_point: HookPoint = HookPoint.POST_MODEL_OUTPUT
    session_id: str = ""
    thread_id: str = ""
    model_output: str = ""           # Raw model output
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class HookResult:
    allow: bool = True
    modified_output: str = ""
    message: str = ""
    metadata: dict = field(default_factory=dict)


class HookRegistry:
    """Plugin-based hook registry."""

    def __init__(self):
        self._hooks: dict[HookPoint, list[Callable]] = {
            hp: [] for hp in HookPoint
        }
        self._async_hooks: dict[HookPoint, list[Callable]] = {
            hp: [] for hp in HookPoint
        }
        self._stats: dict[str, dict] = {}

    def register(self, point: HookPoint, callback: Callable, async_cb: bool = False) -> str:
        """Register a hook callback. Returns hook_id."""
        hook_id = f"hook_{point.value}_{len(self._hooks[point])}"
        if async_cb:
            self._async_hooks[point].append(callback)
        else:
            self._hooks[point].append(callback)
        self._stats[hook_id] = {"calls": 0, "failures": 0, "last_duration_ms": 0}
        return hook_id

    def unregister(self, hook_id: str) -> bool:
        for point in HookPoint:
            for lst in [self._hooks[point], self._async_hooks[point]]:
                for cb in list(lst):
                    if getattr(cb, "__name__", "") == hook_id:
                        lst.remove(cb)
                        return True
        return False

    async def run_hooks(self, point: HookPoint, ctx: HookContext) -> list[HookResult]:
        """Run all hooks registered for a given point. Returns list of results."""
        results = []

        # Sync hooks first
        for cb in self._hooks[point]:
            t0 = time.time()
            try:
                result = cb(ctx)
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, HookResult):
                    results.append(result)
                elif isinstance(result, dict):
                    results.append(HookResult(**result))
                elif result is True or result is None:
                    results.append(HookResult(allow=True))
                elif result is False:
                    results.append(HookResult(allow=False))
            except Exception as e:
                logger.debug(f"Hook {point.value}: {e}")
                results.append(HookResult(allow=True, message=str(e)[:200]))
            dur = (time.time() - t0) * 1000
            self._record_hook_stats(point, dur, isinstance(results[-1], HookResult) and not results[-1].allow)

        # Async hooks
        for cb in self._async_hooks[point]:
            t0 = time.time()
            try:
                result = await cb(ctx)
                if isinstance(result, HookResult):
                    results.append(result)
                elif isinstance(result, dict):
                    results.append(HookResult(**result))
            except Exception as e:
                logger.debug(f"Async hook {point.value}: {e}")
            dur = (time.time() - t0) * 1000
            self._record_hook_stats(point, dur, False)

        return results

    def _record_hook_stats(self, point: HookPoint, duration_ms: float, blocked: bool) -> None:
        key = point.value
        if key not in self._stats:
            self._stats[key] = {"calls": 0, "blocked": 0, "total_duration_ms": 0}
        self._stats[key]["calls"] += 1
        self._stats[key]["total_duration_ms"] += duration_ms
        if blocked:
            self._stats[key]["blocked"] += 1

    def stats(self) -> dict:
        return {
            k: {
                "calls": v.get("calls", 0),
                "blocked": v.get("blocked", 0),
                "avg_ms": round(v.get("total_duration_ms", 0) / max(v.get("calls", 1), 1), 2),
            }
            for k, v in self._stats.items()
        }


# Built-in hooks

async def builtin_approval_hook(ctx: HookContext) -> HookResult:
    """Built-in: approval check before tool execution."""
    if ctx.hook_point != HookPoint.PRE_TOOL_EXEC:
        return HookResult(allow=True)
    try:
        from backend.approval import approval_bridge
        risk = approval_bridge.manager.assess_risk(ctx.tool_name, ctx.tool_args)
        if approval_bridge.manager.needs_approval(risk, ctx.tool_name):
            if ctx.tool_name in ("apply_patch", "file_rw"):
                await approval_bridge.request_file_approval(
                    ctx.session_id,
                    ctx.thread_id,
                    str(ctx.tool_args.get("file_path", ctx.tool_args.get("file", ""))),
                    description=f"{ctx.tool_name}: {str(ctx.tool_args)[:80]}",
                )
            else:
                await approval_bridge.request_command_approval(
                    ctx.session_id,
                    ctx.thread_id,
                    str(ctx.tool_args.get("command", ctx.tool_args)),
                    risk=risk,
                    description=f"{ctx.tool_name}: {str(ctx.tool_args)[:80]}",
                )
    except ImportError:
        pass
    return HookResult(allow=True)


def builtin_metrics_hook(ctx: HookContext) -> HookResult:
    """Built-in: tool metrics recording."""
    if ctx.hook_point == HookPoint.PRE_TOOL_EXEC:
        try:
            from backend.tools.tool_metrics import get_metrics
            get_metrics().record_start(ctx.tool_name, ctx.tool_args)
        except ImportError:
            pass
    elif ctx.hook_point == HookPoint.POST_TOOL_EXEC:
        try:
            from backend.tools.tool_metrics import get_metrics
            success = ctx.tool_result.get("success", True)
            error = ctx.tool_result.get("error", "")
            output = ctx.tool_result.get("output", "")
            get_metrics().record_end(ctx.tool_name, success, error, len(str(output)))
        except ImportError:
            pass
    return HookResult(allow=True)


_registry: Optional[HookRegistry] = None

def get_hook_registry() -> HookRegistry:
    global _registry
    if _registry is None:
        _registry = HookRegistry()
        # Register built-in hooks
        _registry.register(HookPoint.PRE_TOOL_EXEC, builtin_approval_hook, async_cb=True)
        _registry.register(HookPoint.PRE_TOOL_EXEC, builtin_metrics_hook)
        _registry.register(HookPoint.POST_TOOL_EXEC, builtin_metrics_hook)
    return _registry
