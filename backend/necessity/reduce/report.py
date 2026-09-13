"""报告汇总 —— 冗余率 / 致败定位 / 收敛状态。

对应 DIFF_REDUCER.md §5.5 的输出格式。

报告的核心价值（§8.5 原话）：
    「冗余率是一个**可证明**的评测维度。」
    判定用测试做 oracle（移除后验收测试仍通过），不是启发式的行数估计。
    所以即使最小化算法本身不完美，产出的**指标仍有严格定义** ——
    这是一个「即使失败也有产出」的设计。

一条纪律（§7 总原则）：
    「宁可返回未收敛的最优解，也不要返回错误结论。」
    所以 `converged=false` 必须出现在报告里，且 metrics 里要能一眼看到。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .search import SearchResult, redundancy_ratio


@dataclass
class ReduceReport:
    task_id: str = ""
    original: dict = field(default_factory=dict)
    baseline_test: str = ""
    final_test: str = ""

    necessary: dict = field(default_factory=dict)
    redundant: list[dict] = field(default_factory=list)
    culprit: dict | None = None

    metrics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "task_id": self.task_id,
            "original_diff": self.original,
            "baseline_test": self.baseline_test,
            "final_test": self.final_test,
            "necessary": self.necessary,
            "metrics": self.metrics,
        }
        if self.culprit is not None:
            out["culprit"] = self.culprit
        else:
            out["redundant"] = self.redundant
        if self.notes:
            out["notes"] = self.notes
        return out


def _lines(hunks: list) -> tuple[int, int]:
    a = sum(int(getattr(h, "added", 0)) for h in hunks)
    r = sum(int(getattr(h, "removed", 0)) for h in hunks)
    return a, r


def build_report(
    all_hunks: list,
    necessary: SearchResult,
    *,
    task_id: str = "",
    baseline_test: str = "",
    final_test: str = "",
    sandbox_mode: str = "",
    sandbox_degraded: bool = False,
    test_scope_narrowed: bool = False,
    flaky_excluded: int = 0,
    max_hunk_count_exceeded: bool = False,
) -> ReduceReport:
    """组装 §5.5 格式的报告。

    如实标注所有降级与未收敛（§7 总原则）。notes 里的每一条都是
    「这次结论的适用范围」的限定，不是装饰。
    """
    notes: list[str] = []

    nec_ids = {str(getattr(h, "id", h)) for h in necessary.hunks}
    red_hunks = [h for h in all_hunks if str(getattr(h, "id", h)) not in nec_ids]

    add_all, rem_all = _lines(all_hunks)
    add_nec, rem_nec = _lines(necessary.hunks)
    add_red, rem_red = _lines(red_hunks)

    rep = ReduceReport(
        task_id=task_id,
        original={
            "files": len({h.file for h in all_hunks}),
            "hunks": len(all_hunks),
            "lines_added": add_all,
            "lines_removed": rem_all,
        },
        baseline_test=baseline_test,
        final_test=final_test,
        necessary={
            "hunks": [h.id for h in necessary.hunks],
            "lines_added": add_nec,
            "lines_removed": rem_nec,
        },
    )

    for h in red_hunks:
        rep.redundant.append({
            "hunks": [h.id],
            # 判定依据必须写清 —— 这是「可证明」的含义（§8.5）
            "reason": "移除后验收测试仍通过",
            "location": f"{h.file}:{h.new_start}",
            "lines": int(getattr(h, "added", 0)) + int(getattr(h, "removed", 0)),
        })

    rep.metrics = {
        "redundancy_ratio": round(redundancy_ratio(all_hunks, necessary.hunks), 4),
        "necessary_lines": add_nec + rem_nec,
        "redundant_lines": add_red + rem_red,
        "test_runs": necessary.test_runs,
        "cache_hits": necessary.cache_hits,
        # ⚠️ 必须暴露：调用方不得把未收敛结果当作结论
        "converged": necessary.converged,
        "duration_sec": round(necessary.elapsed, 2),
    }

    if not necessary.converged:
        notes.append(
            "未收敛：预算内未搜到最优解，返回的是**当前最优**。"
            + (f" 原因：{necessary.stopped_reason}" if necessary.stopped_reason else "")
        )
    if necessary.monotonic_violations:
        notes.append(
            f"观测到 {necessary.monotonic_violations} 次反直觉交互"
            "（改动更少反而失败）—— 单调性假设失效，结果可能非全局最优（§7 边界 6）"
        )
    if add_nec + rem_nec == 0:
        notes.append(
            "全部改动都是冗余的：删完后测试仍通过，说明 Agent 的改动对目标**无贡献**（§7 边界 4）"
        )
    if not red_hunks:
        notes.append("无冗余改动（redundancy_ratio = 0），说明这次改得很干净（§7 边界 3）")
    if sandbox_degraded:
        notes.append(f"隔离降级为目录复制（{sandbox_mode}）—— 结果等价，但更慢更占空间（§7 边界 1）")
    if not test_scope_narrowed:
        notes.append(
            "测试范围未缩窄（缺影响面分析），按路径匹配或全量跑 —— "
            "这会让单次判定变慢，可能影响收敛率（§6.1）"
        )
    if flaky_excluded:
        notes.append(f"已排除 {flaky_excluded} 个 flaky 测试（§6.2）")
    if max_hunk_count_exceeded:
        notes.append("hunk 数超过 max_hunk_count，已先做文件级粗最小化，粒度偏粗（§6.3）")

    rep.notes = notes
    return rep


def build_culprit_report(
    all_hunks: list,
    culprit: SearchResult,
    *,
    task_id: str = "",
    baseline_test: str = "",
    final_test: str = "",
    notes: list[str] | None = None,
) -> ReduceReport:
    """致败定位的报告（§5.5 的失败场景：输出 culprit 而非 redundant）。"""
    add_c, rem_c = _lines(culprit.hunks)
    rep = ReduceReport(
        task_id=task_id,
        original={
            "files": len({h.file for h in all_hunks}),
            "hunks": len(all_hunks),
            "lines_added": sum(int(getattr(h, "added", 0)) for h in all_hunks),
            "lines_removed": sum(int(getattr(h, "removed", 0)) for h in all_hunks),
        },
        baseline_test=baseline_test,
        final_test=final_test,
        culprit={
            "hunks": [h.id for h in culprit.hunks],
            "locations": [f"{h.file}:{h.new_start}" for h in culprit.hunks],
            "lines_added": add_c,
            "lines_removed": rem_c,
            "reason": "移除这些改动后测试由 fail 变 pass —— 即最小致败集",
        },
        metrics={
            "test_runs": culprit.test_runs,
            "converged": culprit.converged,
            "duration_sec": round(culprit.elapsed, 2),
        },
    )
    n = list(notes or [])
    if not culprit.converged:
        n.append(
            "未收敛：返回的是当前最优致败集，可能不是最小集"
            + (f"。原因：{culprit.stopped_reason}" if culprit.stopped_reason else "")
        )
    rep.notes = n
    return rep


def format_text(rep: ReduceReport) -> str:
    """人读格式（CLI 用）。"""
    d = rep.to_dict()
    lines = [f"任务: {rep.task_id or '(未命名)'}"]
    o = d["original_diff"]
    lines.append(f"原始改动: {o['files']} 文件 / {o['hunks']} hunk / +{o['lines_added']}-{o['lines_removed']}")
    lines.append(f"测试: 基线={rep.baseline_test} 最终={rep.final_test}")

    if rep.culprit is not None:
        c = d["culprit"]
        lines.append("")
        lines.append(f"致败改动: {len(c['hunks'])} 个 hunk")
        for loc in c["locations"]:
            lines.append(f"  - {loc}")
    else:
        n = d["necessary"]
        lines.append("")
        lines.append(f"必要改动: {len(n['hunks'])} 个 hunk / +{n['lines_added']}-{n['lines_removed']}")
        if rep.redundant:
            lines.append(f"冗余改动: {len(rep.redundant)} 处")
            for r in rep.redundant:
                lines.append(f"  - {r['location']} ({r['lines']} 行) — {r['reason']}")

    m = rep.metrics
    lines.append("")
    lines.append(f"冗余率: {m.get('redundancy_ratio', 'n/a')}")
    lines.append(f"判定次数: {m['test_runs']} | 收敛: {'是' if m['converged'] else '否'} | 耗时: {m['duration_sec']}s")
    if rep.notes:
        lines.append("")
        lines.append("注意:")
        for x in rep.notes:
            lines.append(f"  * {x}")
    return "\n".join(lines)
