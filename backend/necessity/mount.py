"""集中式挂载层 —— 宿主侧调用的唯一入口。

为什么需要这一层（而不是在各处直接 `if _nsk_hooks is not None`）：
    INTEGRATION.md §4 要求 10 个挂载点，散落在 graph.py / file_rw.py /
    context_manager.py 三处。若每处都自己写开关判断 + 异常处理，会重复
    10 遍且容易漏掉契约（异常必须放行、超时必须记录）。

    本层把「是否启用」「异常放行」「超时统计」收敛到一处，宿主侧只需：

        from backend.necessity import mount
        mount.on_turn_end(state.total_turns)      # 一行，无需判断

    未启用时这些都是零开销的直通（模块级 _ENABLED 布尔判断）。

契约（INTEGRATION.md §3.3，全部在这里强制）：
    1. 钩子抛异常 -> 放行 + 记录（检查系统故障不得阻塞任务）
    2. 钩子无副作用（除 after_write / scan_workspace / on_task_end）
    3. 主循环路径上不得调 LLM
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger("aurora.necessity.mount")

# ── 开关与超时 ──────────────────────────────────────────────────
# 默认关闭。这是 I1「空操作挂载」的验收要求：未启用时宿主行为必须
# 与完全没有本模块时逐字节一致。
_ENABLED = os.environ.get("AURORA_NECESSITY", "").strip() in ("1", "true", "yes", "on")
_HOOK_TIMEOUT_MS = int(os.environ.get("AURORA_NECESSITY_HOOK_TIMEOUT_MS", "500") or "500")

# 已挂载的实现（None = 未启用）。用模块级可变变量而非每次 import，
# 便于测试注入与运行时切换。
_hooks = None
_stats = {"calls": 0, "errors": 0, "slow": 0}


def is_enabled() -> bool:
    return _hooks is not None


def set_hooks(hooks) -> None:
    """注入实现（测试用；生产由 enable() 装配）。"""
    global _hooks
    _hooks = hooks


def enable(cfg: dict | None = None) -> bool:
    """按配置装配并挂载。返回是否成功挂上。

    失败不抛异常 —— 挂载失败应降级为「不启用」，而不是让 Aurora 起不来。
    """
    global _hooks
    if _hooks is not None:
        return True
    try:
        from backend.necessity.capability import CompositeHooks, load_capabilities

        caps = load_capabilities(cfg or {"enabled": True})
        if not caps:
            logger.info("necessity: 无可用能力，保持未启用")
            return False
        _hooks = CompositeHooks(caps)
        logger.info("necessity hooks mounted: %s", sorted(caps.keys()))
        return True
    except Exception as e:
        logger.warning("necessity 挂载失败，降级为未启用: %s", e)
        _hooks = None
        return False


def _call(label: str, fn, default):
    """统一执行钩子调用：异常放行 + 超时记录（契约 1）。

    注意与 Aurora `approval_gate` 的取舍**相反**：那个是安全组件，
    故障时必须 fail-closed（拒绝）；本层是质量组件，fail-open 才正确 ——
    它不该让任务跑不下去。
    """
    if _hooks is None:
        return default
    _stats["calls"] += 1
    t0 = time.perf_counter()
    try:
        out = fn()
        ms = (time.perf_counter() - t0) * 1000
        if ms > _HOOK_TIMEOUT_MS:
            _stats["slow"] += 1
            logger.warning("necessity hook %s 耗时 %.1fms（预算 %dms）", label, ms, _HOOK_TIMEOUT_MS)
        return out
    except Exception as e:
        _stats["errors"] += 1
        logger.warning("necessity hook %s 失败，放行: %s: %s", label, type(e).__name__, e, exc_info=True)
        return default


def stats() -> dict:
    """钩子调用统计 —— 用于排查「机制没被用起来」。"""
    return dict(_stats, enabled=is_enabled())


# ── 轨迹记录（I2 埋点）──────────────────────────────────────────
# 为什么记在这里而不是各能力内部：mount 是所有钩子的**唯一漏斗** ——
# 在这里记录可以保证「无论哪个能力启用，事实都被采到」，不需要每个能力
# 各自记得调 trace.record（实测此前**无人调用**，导致 Gate 0 永远测不出数）。
#
# INTEGRATION.md §5.2 的原则：**只记录事实，不做判断** ——
# 判断留给 attribution/signals.py，这样信号逻辑改了不用重采数据。
_trace = None
_trace_tried = False


def _get_trace():
    """惰性获取轨迹存储；首次调用时绑定到 .necessity/index.db 以便落盘。

    无 db 时退回纯内存（不报错）—— 轨迹是增强，不该因存储缺失而中断任务。
    """
    global _trace, _trace_tried
    if _trace is not None:
        return _trace
    if _trace_tried:
        return None
    _trace_tried = True
    try:
        from backend.necessity.index.trace import get_trace
        db = None
        try:
            from pathlib import Path
            from backend.necessity.index.store import Store
            db_path = Path.cwd() / ".necessity" / "index.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db = Store(str(db_path))
        except Exception as e:
            logger.debug("necessity trace db 不可用，退回内存: %s", e)
        _trace = get_trace(db=db)
    except Exception as e:
        logger.warning("necessity trace 初始化失败，埋点停用: %s", e)
        _trace = None
    return _trace


def record(kind: str, turn: int = 0, **payload) -> None:
    """记一条事实。任何异常都吞掉 —— 采集不得影响任务（契约 1）。"""
    try:
        t = _get_trace()
        if t is not None:
            t.record(_session_id, kind, turn, **payload)
    except Exception:
        pass


# 当前会话 id / 轮次：on_task_start / on_turn_end 时更新，供 record() 归属。
# ⚠️ 轮次必须真实：Gate 0 的「同一轮内重复读」判定依赖它 ——
# 全部硬编码 turn=0 会让该规则永远误判为「同轮重读」或永远不触发。
_session_id = ""
_current_turn = 0


def flush_trace() -> int:
    """把缓冲的轨迹落库。on_task_end 时调用。"""
    try:
        t = _get_trace()
        return t.flush() if t else 0
    except Exception:
        return 0


# ── 10 个挂载点（INTEGRATION.md §4）─────────────────────────────
# 每个都是「未启用时零开销直通」的一行调用。

def on_task_start(task: dict) -> None:
    """任务开始：编译约束、准备 trace、重置会话状态。"""
    global _session_id
    _session_id = str((task or {}).get("session_id") or "")
    global _current_turn
    _current_turn = 0
    _call("on_task_start", lambda: _hooks.on_task_start(task), None)


def on_turn_end(turn: int) -> None:
    """每轮结束：Guard 后检、状态更新。**Guard 的越界检测靠它。**"""
    global _current_turn
    _current_turn = int(turn or 0)
    _call("on_turn_end", lambda: _hooks.on_turn_end(turn), None)


def on_task_end(result) -> dict:
    """任务结束：产出报告（冗余率 / 约束统计 / 归因）。

    ⚠️ 这是**产出指标的唯一出口** —— 不接它，四个能力的数字永远拿不到。
    """
    out = _call("on_task_end", lambda: _hooks.on_task_end(result), {})
    flushed = flush_trace()
    if flushed:
        logger.debug("necessity trace flushed %d events", flushed)
    return out if isinstance(out, dict) else {}


def before_tool(name: str, arguments: dict, turn: int, call_id: str = ""):
    """预检：可拦截 / 改写。返回 Decision（默认 allow）。"""
    from backend.necessity.hooks import Decision, ToolCall

    if _hooks is None:
        return Decision()
    call = ToolCall(name=name, arguments=arguments, turn=turn, call_id=call_id)
    d = _call("before_tool", lambda: _hooks.before_tool(call), None)
    return d if isinstance(d, Decision) else Decision()


def after_tool(name: str, result: dict, turn: int, call_id: str = "",
               duration_ms: float = 0.0) -> None:
    """后检 + 轨迹记录。归一化宿主的 dict 返回，避免泄漏宿主结构。"""
    if _hooks is None:
        return
    from backend.necessity.hooks import ToolCall, ToolResult

    tr = ToolResult(
        ok=bool(result.get("success")),
        output=str(result.get("output") or ""),
        error=(str(result["error"]) if result.get("error") is not None else None),
        duration_ms=duration_ms,
    )
    call = ToolCall(name=name, arguments={}, turn=turn, call_id=call_id)
    _call("after_tool", lambda: _hooks.after_tool(call, tr), None)
    record("tool_result", turn, tool=name, ok=tr.ok,
           error=tr.error, duration_ms=round(duration_ms, 1))


def read_file(path: str, opts: dict | None = None):
    """接管读文件。**返回 None = 本层不管，宿主按原逻辑读。**

    ⚠️ Context Paging 靠这个挂载点生效 —— 不接它，该能力等于没装。
    """
    if _hooks is None:
        return None
    out = _call("read_file", lambda: _hooks.read_file(path, opts or {}), None)
    # 记事实：本次读取是否命中状态表（命中=省了一次读盘）。
    # Gate 0 的 R_waste 判定需要它 —— 不记就没有重复读取率可算。
    try:
        from pathlib import Path as _P
        content = _P(path)
        record("file_read", _current_turn, path=str(path),
               lines=(content.stat().st_size if content.is_file() else 0),
               cache_hit=out is not None)
    except Exception:
        pass
    return out


def after_write(path: str, writer: str) -> None:
    """写文件后：标记 dirty（writer='agent'）或 stale（其他）。

    ⚠️ 这个区分是「合法重读」与「浪费重读」的分界，
    也是 R/R_waste 两个指标能被分开测量的**唯一**依据。
    """
    _call("after_write", lambda: _hooks.after_write(path, writer), None)
    # 记事实：谁写的（agent / other）。这个区分是「合法重读」与「浪费重读」
    # 的分界 —— 没有它 R/R_waste 无法分开测量。
    record("file_write", _current_turn, path=str(path), writer=str(writer))


def before_compaction(messages: list) -> None:
    """压缩前：快照状态表版本（不得被压缩影响）。"""
    _call("before_compaction", lambda: _hooks.before_compaction(messages), None)
    record("compaction", _current_turn, message_count=len(messages or []))


def after_compaction(summary: str) -> str:
    """压缩后：返回增强的 summary（注入 file_state 索引）。"""
    out = _call("after_compaction", lambda: _hooks.after_compaction(summary), summary)
    return out if isinstance(out, str) else summary


def scan_workspace() -> list:
    """扫描工作区变更（Guard 后检的权威数据源）。

    不由宿主自动触发 —— Guard 的 interceptor 内部调用。
    """
    out = _call("scan_workspace", lambda: _hooks.scan_workspace(), [])
    return out if isinstance(out, list) else []


# 启动时尝试装配（失败则保持未启用，不影响 Aurora）
if _ENABLED:
    try:
        enable()
    except Exception as e:  # pragma: no cover - enable() 已自行兜异常
        logger.warning("necessity 启动装配失败: %s", e)
