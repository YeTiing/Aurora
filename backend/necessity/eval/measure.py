"""把一次 Agent 产出变成完整的 Attempt —— EVAL.md §2.4 / §7.4 的落地点。

为什么单独成文件：这里的每个数字都直接喂给 gate 判据，
字段拼接错误会让 gate 静默失效（比崩溃更危险）。集中一处便于逐个核对。

只做「事实 → 指标」的换算，不含任何调度逻辑。
"""
from __future__ import annotations

from typing import Any

from backend.necessity.eval.harness import text_level_violations
from backend.necessity.eval.plan import spec_of
from backend.necessity.eval.records import Attempt, GateMetrics, make_attempt_id


def events_to_objects(events: list[dict]):
    """dict → AgentEvent（Gate 0 的 classify_reads 需要对象而非 dict）。

    这里不 import AgentEvent 到模块顶层：core 的导入会拉起 SQLite 等依赖，
    让「只读报告」的场景也被迫加载它们，没必要。
    """
    from typing import cast

    from backend.necessity.index.trace import AgentEvent

    out = []
    for e in events or []:
        out.append(AgentEvent(
            session_id=str(e.get("session_id") or ""),
            kind=cast(Any, str(e.get("kind") or "file_read")),
            turn=int(e.get("turn") or 0), ts=float(e.get("ts") or 0.0),
            payload=dict(e.get("payload") or {}),
        ))
    return out


def _diff_stats_from_text(diff_text: str) -> dict:
    """没给 diff_stats 时从 unified diff 现算（+/- 行，跳过文件头）。"""
    added = sum(1 for ln in diff_text.splitlines()
                if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_text.splitlines()
                  if ln.startswith("-") and not ln.startswith("---"))
    return {"added": added, "removed": removed, "total": added + removed}


def measure(task: Any, arm: str, run_index: int, result, started: float,
            ended: float) -> Attempt:
    """把一次 Agent 产出变成完整的 Attempt（§7.4 的字段一个不少）。"""
    from backend.necessity.eval.gate0 import classify_reads

    spec = spec_of(task)
    task_id = spec.task_id
    events = list(result.events or [])

    # Gate 0：只读事实数据算重复读取率（§2.4 的精确定义在 classify_reads 里）
    g0 = classify_reads(events_to_objects(events)) if events else None
    gates = GateMetrics(
        reads_total=g0.total_reads if g0 else 0,
        reads_waste=g0.waste_reads if g0 else 0,
        tokens_total=int(result.tokens or 0),
        compaction_count=sum(1 for e in events if e.get("kind") == "compaction"),
        constraint_violations=sum(1 for e in events
                                  if e.get("kind") == "constraint_violation"),
    )
    _fill_constraint_metrics(gates, spec, result, events)

    diff_stats = dict(result.diff_stats or {})
    if not diff_stats and result.diff_text:
        diff_stats = _diff_stats_from_text(result.diff_text)

    meta = dict(result.meta or {})
    if arm == "C_prime" and spec.constraints:
        _apply_text_level_check(meta, gates, spec, result, events)
    if result.status == "timeout":
        meta["early_stop"] = True     # §7.3 早停：超轮次上限计入 fail

    return Attempt(
        attempt_id=make_attempt_id(task_id, arm, run_index),
        task_id=task_id, arm=arm, run_index=run_index,
        category=spec.category, started_at=started, ended_at=ended,
        status=result.status, turns=int(result.turns or 0),
        tokens=int(result.tokens or 0),
        diff_text=result.diff_text or "", diff_stats=diff_stats,
        events=events, gates=gates, error=result.error or "", meta=meta,
    )


def _fill_constraint_metrics(gates: GateMetrics, spec, result, events: list[dict]) -> None:
    """约束保持率 ρ（§2.2）：ρ = min(s, T) / T，s = 首次违反轮次，未违反则 s = T+1。

    归一化的理由是「15 轮未违反 ≠ 40 轮未违反」；没有约束的任务记为 -1，
    报告侧靠 -1 把它们排除，而不是当成 ρ=0（那会把「无约束」误读成「全违反」）。
    """
    if not spec.constraints:
        gates.constraint_survivals = -1
        return
    turns = max(1, int(result.turns or 0))
    viol_turns = [int(e.get("turn") or 0) for e in events
                  if e.get("kind") == "constraint_violation"]
    s = min(viol_turns) if viol_turns else turns + 1
    gates.constraint_survivals = s
    gates.constraint_rho = min(s, turns) / turns


def _apply_text_level_check(meta: dict, gates: GateMetrics, spec,
                            result, events: list[dict]) -> None:
    """C′ 的越界只能用文本层面判断（无 Guard、无结构化事件通道）。"""
    changed = sorted({str(e["payload"].get("path", "")) for e in events
                      if e.get("kind") == "file_write" and e.get("payload")})
    changed = [p for p in changed if p]
    viol = text_level_violations(spec.constraints, changed)
    gates.constraint_violations = len(viol)
    if viol:
        turns = max(1, int(result.turns or 0))
        gates.constraint_survivals = 1
        gates.constraint_rho = 1.0 / turns
    meta["text_violations"] = viol
