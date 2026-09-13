"""确定性信号规则（§4.2 信号表的具体实现）。

分工：signals.py 放类型/常量/索引/优先级；本文件每条规则一个纯函数，
输入 `_Index`，输出 `Signal` 列表。拆开是因为信号逻辑会随分类体系
迭代而变（§6.3），独立函数可单独替换、单独测试，且不影响已采集数据。
"""
from __future__ import annotations

from typing import Any

from .signals import (
    EDIT_ERROR_TYPES, ENV_ERROR_TYPES, LATE_WRITE_RATIO, THRASH_THRESHOLD,
    TOOL_ERROR_TYPES, Signal, _agent_writes, _env_error_of, _Index, _load, _p,
)
from .taxonomy import (
    CAPABILITY, CONSTRAINT_DRIFT, CONTEXT_MISSING, CONTEXT_STALE, EDIT_ERROR,
    ENVIRONMENT, PLANNING, TASK_ISSUE, THRASHING, TOOL_USE,
)


# ── 环境 / 任务（最高优先级：先排除「不是 Agent 的锅」）──────────

def _sig_environment(idx: _Index) -> list[Signal]:
    """环境类异常 / flaky / 无改动即失败 → environment（§4.2，高）。"""
    out: list[Signal] = []
    for t in idx.tests:
        if str(_p(t, "result", "")).lower() != "fail":
            continue
        et, turn = _env_error_of(t), getattr(t, "turn", 0)
        if et in ENV_ERROR_TYPES:
            out.append(Signal(
                "env_error", ENVIRONMENT, 0.9, turn,
                f"第 {turn} 轮测试失败，错误为环境类（{et}）",
            ))
        # §4.2「测试 flaky」：同 suite 结果不一致 → 与 Agent 改动无关
        suite = _p(t, "suite")
        results = {str(_p(x, "result", "")).lower()
                   for x in idx.tests if _p(x, "suite") == suite}
        if suite and {"pass", "fail"} <= results:
            out.append(Signal(
                "flaky_test", ENVIRONMENT, 0.75, turn,
                f"测试集 {suite} 多次运行结果不一致（同配置既有 pass 又有 fail），属 flaky",
            ))
    if idx.tests and not _agent_writes(idx.writes):
        fails = [t for t in idx.tests if str(_p(t, "result", "")).lower() == "fail"]
        if fails:
            out.append(Signal(
                "test_fail_without_change", ENVIRONMENT, 0.65,
                getattr(fails[0], "turn", 0),
                "测试失败但本会话没有任何 Agent 文件改动，失败不可能由 Agent 编辑造成",
            ))
    uniq: dict[tuple[str, int], Signal] = {}
    for s in out:  # flaky 可能对每个 fail 都重复记一次，按 (signal, turn) 去重
        uniq.setdefault((s.name, s.turn), s)
    return list(uniq.values())


def _sig_task_issue(idx: _Index, task: dict | None) -> list[Signal]:
    """任务本身不可完成 / 基线就是挂的 → task_issue（§4.2，高）。

    输入来自**任务集元数据**（人工预标注）或基线的 test_run —— 这是
    非 Agent 问题，必须最高优先级先排除（§2.3 / §4.3）。
    """
    out: list[Signal] = []
    meta = task or {}
    if meta.get("unachievable"):
        out.append(Signal(
            "task_unachievable", TASK_ISSUE, 0.95, 0,
            "任务集元数据预标注：该任务不可完成 / 需求矛盾",
        ))
    if meta.get("baseline_fail"):
        out.append(Signal(
            "baseline_fail", TASK_ISSUE, 0.95, 0,
            "基线 T(∅) = fail：起始快照下验收测试就是失败的，任务本身有问题",
        ))
    for t in idx.tests:
        if _p(t, "baseline") is True and str(_p(t, "result", "")).lower() == "fail":
            out.append(Signal(
                "baseline_fail", TASK_ISSUE, 0.95, getattr(t, "turn", 0),
                f"基线测试（turn {getattr(t, 'turn', 0)}）在无改动时即失败，任务本身有问题",
            ))
    return out


# ── 约束 / 抖动 / 工具 ───────────────────────────────────────────

def _sig_constraint_violation(idx: _Index) -> list[Signal]:
    """约束违反 → constraint_drift（§4.2，高）。Guard 的客观日志。"""
    if not idx.violations:
        return []
    v = idx.violations[0]
    cid = _p(v, "constraint_id") or _p(v, "rule") or "未命名约束"
    return [Signal(
        "constraint_violation", CONSTRAINT_DRIFT, 0.95, getattr(v, "turn", 0),
        f"第 {getattr(v, 'turn', 0)} 轮触发约束违反（{cid}），共 {len(idx.violations)} 次",
    )]


def _sig_repeated_revert(idx: _Index) -> list[Signal]:
    """同一位置反复改-回滚 → thrashing（§4.2，高）。"""
    turns: dict[str, list[int]] = {}
    for e in list(idx.writes) + list(idx.reverts):
        path = _p(e, "path")
        if path:
            turns.setdefault(path, []).append(getattr(e, "turn", 0))
    out: list[Signal] = []
    for path, ts in turns.items():
        reverts = sum(1 for e in idx.reverts if _p(e, "path") == path)
        if reverts >= THRASH_THRESHOLD or len(ts) >= THRASH_THRESHOLD + 1:
            out.append(Signal(
                "repeated_revert", THRASHING, 0.8, min(ts),
                f"{path} 上出现 {reverts} 次回滚 / {len(ts)} 次改动，反复改-回滚不收敛",
            ))
    return out


def _sig_tool_error(idx: _Index) -> list[Signal]:
    """工具参数错误 / 工具失败未补救 → tool_use（§4.2，高/中）。"""
    out: list[Signal] = []
    for e in idx.all:
        et = _env_error_of(e)
        if et in TOOL_ERROR_TYPES or _p(e, "tool_failed") is True:
            out.append(Signal(
                "tool_error", TOOL_USE, 0.9, getattr(e, "turn", 0),
                f"第 {getattr(e, 'turn', 0)} 轮工具调用失败（{et or 'tool_failed'}）",
            ))
    if out:
        last = max(s.turn for s in out)
        if not any(getattr(w, "turn", 0) > last for w in _agent_writes(idx.writes)):
            out.append(Signal(
                "tool_failure_unhandled", TOOL_USE, 0.6, last,
                "工具失败后 Agent 未做任何后续补救改动",
            ))
    return out


def _sig_edit_error(idx: _Index) -> list[Signal]:
    """写入后测试报语法/缩进错误 → 编辑本身写错 → edit_error。"""
    out: list[Signal] = []
    for t in idx.tests:
        if str(_p(t, "result", "")).lower() != "fail":
            continue
        et = _env_error_of(t)
        if et in EDIT_ERROR_TYPES and any(
            getattr(w, "turn", 0) <= getattr(t, "turn", 0)
            for w in _agent_writes(idx.writes)
        ):
            out.append(Signal(
                "syntax_error_after_write", EDIT_ERROR, 0.85, getattr(t, "turn", 0),
                f"第 {getattr(t, 'turn', 0)} 轮测试报 {et}，出现在写入改动之后，"
                f"说明编辑本身写错",
            ))
    return out


# ── 上下文类 ─────────────────────────────────────────────────────

def _sig_modified_unread(idx: _Index) -> list[Signal]:
    """改了没读过的文件 → context_missing（§4.2，置信度中高）。"""
    read_paths = {_p(r, "path") for r in idx.reads if _p(r, "path")}
    out: list[Signal] = []
    seen: set[str] = set()
    for w in _agent_writes(idx.writes):
        path = _p(w, "path")
        if path and path not in read_paths and path not in seen:
            seen.add(path)
            out.append(Signal(
                "modified_unread", CONTEXT_MISSING, 0.7, getattr(w, "turn", 0),
                f"第 {getattr(w, 'turn', 0)} 轮修改了从未读过的 {path}",
            ))
    return out


def _sig_compaction_reread(idx: _Index) -> list[Signal]:
    """压缩后又重读同一文件 → 关键信息被压缩掉 → context_missing（置信度高）。"""
    seen: set[tuple[str, int]] = set()
    out: list[Signal] = []
    for c in idx.compactions:
        cturn = getattr(c, "turn", 0)
        before = {_p(r, "path") for r in idx.reads
                  if getattr(r, "turn", 0) < cturn and _p(r, "path")}
        for r in idx.reads:
            rt = getattr(r, "turn", 0)
            if rt > cturn and _p(r, "path") in before and ("compaction_reread", rt) not in seen:
                seen.add(("compaction_reread", rt))
                out.append(Signal(
                    "compaction_reread", CONTEXT_MISSING, 0.85, rt,
                    f"第 {cturn} 轮发生压缩后，第 {rt} 轮重读了 "
                    f"{_p(r, 'path')}，说明该内容已被压缩丢弃",
                ))
    return out


def _sig_context_near_limit(idx: _Index) -> list[Signal]:
    """多次压缩 → 上下文长期接近上限 → context_missing（§4.2，中）。"""
    if len(idx.compactions) >= 2:
        return [Signal(
            "context_near_limit", CONTEXT_MISSING, 0.5,
            getattr(idx.compactions[0], "turn", 0),
            f"会话内发生 {len(idx.compactions)} 次上下文压缩，上下文长期接近上限",
        )]
    return []


def _sig_stale_after_external_change(idx: _Index) -> list[Signal]:
    """读后被外部改动，Agent 仍按旧内容写 → context_stale。

    「过期信息」的客观判据：Agent 的决策基于某次读取，而该文件在读取之后
    被 Agent 之外的一方改过。这是确定性可判的，不需要读思考文本。
    """
    last_read: dict[str, int] = {}
    for r in idx.reads:
        if _p(r, "path"):
            last_read[_p(r, "path")] = getattr(r, "turn", 0)
    external: dict[str, int] = {}
    for w in idx.writes:
        if _p(w, "writer", "agent") != "agent" and _p(w, "path"):
            external[_p(w, "path")] = getattr(w, "turn", 0)
    out: list[Signal] = []
    for w in _agent_writes(idx.writes):
        path = _p(w, "path")
        rt, et = last_read.get(path), external.get(path)
        if rt is not None and et is not None and rt < et <= getattr(w, "turn", 0):
            out.append(Signal(
                "stale_after_external_change", CONTEXT_STALE, 0.75,
                getattr(w, "turn", 0),
                f"{path} 在第 {et} 轮被外部改动，但 Agent 仍按第 {rt} 轮的旧内容"
                f"在第 {getattr(w, 'turn', 0)} 轮写入",
            ))
    return out


# ── 规划 / 能力（低置信度打底，主要靠 Layer 2 补齐）──────────────

def _sig_planning(idx: _Index, result: Any) -> list[Signal]:
    """首次写入过晚 → 疑似规划/方向问题 → planning（§4.3，中低置信度）。

    §4.3 指出 planning 主要依赖 Layer 2。这里给一个确定性打底：
    绝大部分轮次花在探索、很晚才动手，且最终失败 —— 说明没有形成有效计划。
    """
    writes = _agent_writes(idx.writes)
    if not writes or result is None or getattr(result, "ok", True):
        return []
    turns = int(getattr(result, "turns", 0) or 0)
    first = min(getattr(w, "turn", 0) for w in writes)
    if turns >= 5 and first > LATE_WRITE_RATIO * turns:
        return [Signal(
            "late_first_write", PLANNING, 0.55, first,
            f"首次改动发生在第 {first} / {turns} 轮"
            f"（>{int(LATE_WRITE_RATIO * 100)}%），"
            f"绝大部分预算用于探索、未先形成有效计划即失败",
        )]
    return []


def _sig_capability(idx: _Index, result: Any) -> list[Signal]:
    """轮次耗尽仍有改动但未收敛 → capability（兜底，低置信度）。

    优先级最低：只在没有更具体信号时才成为 primary。它描述的是
    「排除其他原因后，确实像能力不足」，不是强结论。
    """
    if result is None or getattr(result, "ok", True) or not _agent_writes(idx.writes):
        return []
    turns = int(getattr(result, "turns", 0) or 0)
    budget = int((getattr(result, "diff_stats", {}) or {}).get("max_turns", 0) or 0)
    if budget and turns >= budget:
        return [Signal(
            "budget_exhausted", CAPABILITY, 0.4, turns,
            f"轮次耗尽（{turns}/{budget}）且改动未收敛；"
            f"排除其他信号后指向能力不足",
        )]
    return []


# ── 入口 ──────────────────────────────────────────────────────────

def extract_signals(trace: Any, session_id: str = "", task: dict | None = None,
                    result: Any = None) -> list[Signal]:
    """提取全部确定性信号。纯函数，无副作用，不调 LLM。

    调用顺序不影响结果 —— primary 由 `pick_primary` 按 §4.3 优先级选。
    """
    idx = _load(trace, session_id)
    out: list[Signal] = []
    out += _sig_task_issue(idx, task)
    out += _sig_environment(idx)
    out += _sig_constraint_violation(idx)
    out += _sig_repeated_revert(idx)
    out += _sig_tool_error(idx)
    out += _sig_modified_unread(idx)
    out += _sig_compaction_reread(idx)
    out += _sig_context_near_limit(idx)
    out += _sig_stale_after_external_change(idx)
    out += _sig_edit_error(idx)
    out += _sig_planning(idx, result)
    out += _sig_capability(idx, result)
    return out


__all__ = ["extract_signals"]
