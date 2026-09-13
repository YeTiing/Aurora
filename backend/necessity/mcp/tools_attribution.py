"""Failure Attribution 的 MCP 工具。

INTEGRATION.md §1.3：✅ 完全可以 MCP 化（离线分析，不需要宿主循环）。

⚠️ 证据不是可选项（ATTRIBUTION.md §3.3）：
    primary 非 unknown ⇒ evidence 必须非空 —— 没有证据的归因等于猜。
    所以返回文本里必须把证据逐条列出，让调用方能判断这是结论还是噪声。

已知接缝（诚实说明）：轨迹设计上走 core/index/store.py，但 Store 目前
尚未实现 `append_agent_events` / `query_agent_events`（只有 trace.py 按
鸭子类型调用）。这是 core 层的缺口，本层不能越过硬约束去修；因此这里
与 CLI 同款地退回 `cli.attribution_cmd.TraceDB` 最小适配器。Store 补齐
后本层无需改动（优先走 Store 的分支已经写好）。
"""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolOutcome, ToolError, obj_schema, optional, require

__all__ = ["attribution_report", "ATTRIBUTION_TOOLS"]


class _FailedAttempt:
    """最小 TaskResult 替身 —— classifier 只鸭子类型访问这三个属性。

    CLI 为什么也这么做：MCP 层没有真实的 TaskResult 对象，而归因只针对
    失败尝试，所以固定 ok=False（成功尝试归因返回 None，不是本工具的用途）。
    """

    def __init__(self, turns: int = 0):
        self.ok = False
        self.turns = turns
        self.diff_stats: dict = {}


def _open_trace(db_path: str):
    """打开轨迹库。缺 db / 库不存在 -> 报参数级错误（不静默给出空结论）。"""
    if not db_path:
        return None
    if db_path != ":memory:" and not Path(db_path).exists():
        raise ToolError(
            f"轨迹库不存在: {db_path}。"
            "提示：轨迹库是 trace 采集落库的 SQLite 文件（INTEGRATION.md §5.3）。",
            code=-32000,
        )
    try:
        from backend.necessity.index.store import Store

        store = Store(db_path)
        if hasattr(store, "query_agent_events"):
            return store
    except Exception:
        pass
    # Store 尚未实现事件读写 -> 最小适配器（见模块 docstring 的已知接缝）
    from cli.attribution_cmd import TraceDB

    return TraceDB(db_path)


def attribution_report(params: dict) -> ToolOutcome:
    """从轨迹库归因一个失败会话，返回分类 + 证据 + Gate-6 覆盖检查。"""
    from backend.necessity.attribution import FailureClassifier, check_signal_coverage
    from backend.necessity.index.trace import TraceStore

    session_id = require(params, "session_id", str)
    if not session_id.strip():
        raise ToolError("'session_id' 不能为空")
    db_path = optional(params, "db", str, "")
    turns = optional(params, "turns", int, 0)
    if turns < 0:
        raise ToolError("'turns' 不能为负")

    trace = TraceStore(db=_open_trace(db_path), flush_every=1)
    classifier = FailureClassifier(trace=trace, llm=None)
    res = classifier.attribute(_FailedAttempt(turns=turns), task={},
                               session_id=session_id)
    if res is None:   # attribute 对失败不会返回 None，防御性兜底
        raise ToolError(f"会话 {session_id!r} 未能归因（classifier 返回 None）",
                        code=-32000)

    coverage = check_signal_coverage([res], trace=trace)
    structured = {
        "session_id": session_id,
        "attribution": res.as_dict(),
        "gate6": coverage.as_dict(),
    }
    return ToolOutcome(text=_format(session_id, res, coverage), structured=structured)


def _format(session_id: str, res, coverage) -> str:
    lines = [
        f"会话: {session_id}",
        f"归因: {res.primary} (method={res.method}, confidence={res.confidence:.2f})",
        f"Agent 责任: {'是' if res.agent_fault else '否'}",
        f"改进方向: {res.improvement}",
        "",
        f"证据 ({len(res.evidence)} 条):",
    ]
    if not res.evidence:
        lines.append("  （无 —— unknown 允许无证据；其他类别不应为空）")
    for e in res.evidence:
        lines.append(
            f"  - [turn {e.get('turn', '?')}] {e.get('signal')}: {e.get('detail')}")
    if res.contributing:
        lines.append(f"并发因素: {', '.join(res.contributing)}")
    if res.note:
        lines.append(f"备注: {res.note}")
    if res.conflict:
        lines.append(f"冲突: {res.conflict}")
    lines.append("")
    lines.append(
        f"Gate 6 覆盖: unknown {coverage.unknown_ratio:.0%}，"
        f"{'不足 —— 需补埋点' if coverage.insufficient else '充分'}"
    )
    if coverage.missing_event_kinds:
        lines.append(f"  从未采集的事件: {', '.join(coverage.missing_event_kinds)}")
    return "\n".join(lines)


ATTRIBUTION_TOOLS = [
    Tool(
        name="attribution_report",
        description=(
            "Failure Attribution：从轨迹 SQLite 库对一个失败会话归因，"
            "返回分类、逐条证据、改进方向与 Gate-6 信号覆盖检查。离线只读。"
        ),
        input_schema=obj_schema({
            "session_id": {"type": "string", "description": "会话 id"},
            "db": {"type": "string",
                   "description": "轨迹 SQLite 库路径（可缺省 → 无轨迹，标 unattributable）"},
            "turns": {"type": "integer", "minimum": 0, "default": 0,
                      "description": "该会话总轮次（影响 planning/capability 类信号）"},
        }, ["session_id"]),
        fn=attribution_report,
        annotations={"readOnlyHint": True},
    ),
]
