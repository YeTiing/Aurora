"""Necessity 钩子分发 —— 宿主侧唯一的接入面。

原本在 adapter/aurora/ 下（作为「适配另一个宿主」的层）。合并进 Aurora 后
宿主就是本仓库，这层间接不再需要 —— 直接作为 necessity 包的 adapter 模块。

职责不变：把 Aurora 主循环里的调用点安全转发给能力实现。
**本模块不含任何能力逻辑**，只有分发、异常隔离、超时统计。

三条契约（INTEGRATION.md §3.3）强制在这里执行：
  1. 钩子抛异常 -> 放行 + 记录（检查系统故障不得阻塞任务）
  2. 钩子无副作用（除 after_write / scan_workspace / on_task_end）
  3. 主循环路径上不得调 LLM

"""

import logging
import time
from typing import Any

from backend.necessity.hooks import (
    Decision,
    FileChange,
    NecessityHooks,
    NullHooks,
    ReadResult,
    TaskResult,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger("aurora.necessity.adapter")

# 默认超时（INTEGRATION.md §3.3）
DEFAULT_HOOK_TIMEOUT_MS = 500

# 当前挂载的实现。默认 NullHooks —— 未启用任何能力时宿主行为完全不变。
_hooks: NecessityHooks = NullHooks()
_hook_timeout_ms: int = DEFAULT_HOOK_TIMEOUT_MS

# 统计：钩子抛异常/超时的次数。用于「机制没被用起来」的排查
# （EVAL.md §6.5 失败判据之一：某能力调用次数接近 0）。
_stats: dict[str, int] = {"errors": 0, "timeouts": 0, "calls": 0}


def set_hooks(hooks: NecessityHooks | None) -> None:
    """挂载实现。传 None 等价于卸载（退回 NullHooks）。"""
    global _hooks
    _hooks = hooks or NullHooks()
    logger.info("necessity hooks mounted: %s", type(_hooks).__name__)


def get_hooks() -> NecessityHooks:
    return _hooks


def set_hook_timeout(ms: int) -> None:
    global _hook_timeout_ms
    _hook_timeout_ms = max(0, int(ms))


def hook_stats() -> dict[str, int]:
    return dict(_stats)


def _safe(label: str, fn, default):
    """执行钩子调用，任何异常都退回 default（契约 1：放行 + 记录）。

    这是本模块存在的核心理由：**检查系统故障不得阻塞任务**。
    注意与 Aurora `approval_gate` 的取舍相反 —— 那个是安全组件，
    故障时必须 fail-closed；本层是质量组件，fail-open 才是对的。
    """
    _stats["calls"] += 1
    start = time.perf_counter()
    try:
        result = fn()
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms > _hook_timeout_ms:
            # 不中断，只记录 —— 中途放弃反而可能留下半完成状态
            _stats["timeouts"] += 1
            logger.warning(
                "necessity hook %s took %.1fms (budget %dms)",
                label, elapsed_ms, _hook_timeout_ms,
            )
        return result
    except Exception as e:
        _stats["errors"] += 1
        logger.warning(
            "necessity hook %s failed, allowing through: %s: %s",
            label, type(e).__name__, e, exc_info=True,
        )
        return default


# ── 供 Aurora 调用的薄接口 ────────────────────────────────────────
# 每个函数都不抛异常。Aurora 侧因此不需要再包 try/except。

def on_task_start(task: dict) -> None:
    _safe("on_task_start", lambda: _hooks.on_task_start(task), None)


def on_turn_end(turn: int) -> None:
    _safe("on_turn_end", lambda: _hooks.on_turn_end(turn), None)


def on_task_end(result: TaskResult) -> dict[str, Any]:
    out = _safe("on_task_end", lambda: _hooks.on_task_end(result), {})
    # 返回类型不对时不能让非法对象泄漏给宿主（与 before_tool 同款防护）
    return out if isinstance(out, dict) else {}


def before_tool(name: str, arguments: dict, turn: int, call_id: str = "") -> Decision:
    """预检。任何故障都返回 allow（默认 Decision）。"""
    call = ToolCall(name=name, arguments=arguments, turn=turn, call_id=call_id)
    decision = _safe("before_tool", lambda: _hooks.before_tool(call), None)
    if not isinstance(decision, Decision):
        return Decision()
    return decision


def after_tool(name: str, result: dict, turn: int, call_id: str = "",
               duration_ms: float = 0.0) -> None:
    """后检 + 轨迹记录。

    Aurora 的工具返回是 dict（{"success","output","error"}），这里做一次
    归一化，避免把宿主的返回结构泄漏进 core。
    """
    tr = ToolResult(
        ok=bool(result.get("success")),
        output=str(result.get("output") or ""),
        error=(str(result["error"]) if result.get("error") is not None else None),
        duration_ms=duration_ms,
    )
    call = ToolCall(name=name, arguments={}, turn=turn, call_id=call_id)
    _safe("after_tool", lambda: _hooks.after_tool(call, tr), None)


def read_file(path: str, opts: dict | None = None) -> ReadResult | None:
    """接管读文件。返回 None = 本层不管，宿主按原逻辑读。"""
    return _safe("read_file", lambda: _hooks.read_file(path, opts or {}), None)


def after_write(path: str, writer: str) -> None:
    _safe("after_write", lambda: _hooks.after_write(path, writer), None)


def before_compaction(messages: list) -> None:
    _safe("before_compaction", lambda: _hooks.before_compaction(messages), None)


def after_compaction(summary: str) -> str:
    out = _safe("after_compaction", lambda: _hooks.after_compaction(summary), summary)
    return out if isinstance(out, str) else summary


def scan_workspace() -> list[FileChange]:
    out = _safe("scan_workspace", lambda: _hooks.scan_workspace(), [])
    return out if isinstance(out, list) else []


# ── 挂载点与开关（原 adapter/aurora/mount.py 合并而来）──────────
"""Aurora 挂载点 —— 记录「钩子该接在哪」，供宿主侧注入使用。

为什么单独一个文件而不是直接改 Aurora：
  INTEGRATION.md §1.2 要求**非侵入式**。Aurora 的改动必须是「一行调用 +
  可选导入」，出问题能一键关闭。本文件是那「一行调用」的宿主侧入口，
  集中说明每个挂载点的位置与契约，避免散落在 Aurora 各处难以审计。
"""

import logging
import os
from typing import Any

logger = logging.getLogger("necessity.adapter.aurora.mount")

# 挂载点清单（对应 INTEGRATION.md §4）。
# 行号**不作为契约** —— 宿主演进会让它漂移（本项目已发生过一次）。
# 定位以符号名为准。
MOUNT_POINTS = {
    "on_task_start": "AgentGraph.run / run_with_stream 入口",
    "before_tool": "AgentGraph._run_executor 内的 handler（包裹外层）",
    "after_tool": "同 handler（返回后）",
    "read_file": "tools/file_rw.py::file_rw_handler 的 read 分支",
    "after_write": "tools/file_rw.py 的 write/delete/copy/move 分支",
    "on_turn_end": "AgentGraph 主循环每轮末尾（`_run_observer` 之后）",
    "before_compaction": "context/context_manager.py::compact 入口",
    "after_compaction": "同上，返回前",
    "on_task_end": "AgentGraph.run / run_with_stream 返回前",
    "scan_workspace": "Guard 后检调用（不由宿主自动触发）",
}

# 关键设计说明：
# before_tool / after_tool 包在 handler 外层，**不改 ToolRegistry.execute**。
# 理由（INTEGRATION.md §4）：ToolRegistry 是通用基础设施，改它会影响所有
# 调用方（含子 Agent、CLI、MCP 代理）；包在 Agent 的 handler 层更聚焦，
# 也更容易开关。


def is_enabled() -> bool:
    """总开关。默认关闭 —— 未显式启用时宿主行为必须完全不变。"""
    return os.environ.get("NECESSITY_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def install(config: dict[str, Any] | None = None) -> bool:
    """按配置挂载钩子。返回是否真的挂上了实现。

    默认返回 False（不挂载）—— 这是 I1「空操作挂载」的要求：
    先证明钩子层不影响原有行为，再往里填逻辑。
    """
    if not is_enabled() and not (config or {}).get("enabled"):
        logger.info("necessity disabled (set NECESSITY_ENABLED=1 to enable)")
        return False

    from backend.necessity.adapter import set_hook_timeout, set_hooks

    cfg = config or {}
    set_hook_timeout(int(cfg.get("hook_timeout_ms", 500)))

    # 能力的挂载顺序：Context Paging 默认开（风险低），Guard 默认 warn，
    # Reducer / Attribution 默认关（INTEGRATION.md §8.1）。
    # 具体实现由各能力模块提供；此处只做装配，缺失的实现一律跳过，
    # 保证「部分能力未就绪」不影响整体可用。
    try:
        from backend.necessity.context import build_context_hooks
    except ImportError:
        build_context_hooks = None  # type: ignore[assignment]

    if build_context_hooks is None:
        logger.info("necessity: no core implementation available yet, staying in null mode")
        return False

    set_hooks(build_context_hooks(cfg))
    return True
