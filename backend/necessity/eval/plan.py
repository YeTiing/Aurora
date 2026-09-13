"""跑分矩阵的展开与裁剪 —— EVAL.md §3.3 / §7.1 / §7.3。

单独成文件的核心目的是**让算力可见**：任何人想跑分，第一件事是拿到
一个 Plan 并看到总数。§7.2 说 690 次约 3.8 天 —— 这个数字必须在跑之前
就摆在台面上，而不是跑完才知道。

「按需裁剪」的判据（D 组）刻意做成可覆盖的三级回退，因为任务集的
meta.json 还在演进：显式 large_diff > diff_lines/lines_changed > min_lines_changed。
缺失时按「不算大」处理 —— 宁少跑，不虚报。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from backend.necessity.eval.harness import LARGE_DIFF_MIN_LINES
from backend.necessity.eval.records import ARMS, ARM_TASK_FILTER, Attempt, TaskSpec

# 兼容 spec 里写的 ARMS_TASK_FILTER 名字（records.py 实际叫 ARM_TASK_FILTER）
ARMS_TASK_FILTER = ARM_TASK_FILTER

DEFAULT_MINUTES_PER_RUN = 8.0     # §7.2 的估算基准


def spec_of(task: Any) -> TaskSpec:
    """兼容 LoadedTask（有 .spec）与裸 TaskSpec。"""
    return getattr(task, "spec", task)


def repo_of(task: Any, task_id: str) -> Path:
    r = getattr(task, "repo", None)
    return Path(r) if r is not None else Path(str(task_id))


def is_large_diff(spec: TaskSpec) -> bool:
    """D 组筛选（§3.3「D 只跑改动量大的」）。"""
    meta = spec.meta or {}
    if "large_diff" in meta:
        return bool(meta["large_diff"])
    for key in ("diff_lines", "lines_changed"):
        if key in meta:
            try:
                return int(meta[key]) >= LARGE_DIFF_MIN_LINES
            except (TypeError, ValueError):
                pass
    return int(spec.min_lines_changed or 0) >= LARGE_DIFF_MIN_LINES


def tasks_for_arm(arm: str, tasks: Sequence[Any]) -> list[Any]:
    """按 ARM_TASK_FILTER 裁剪该 arm 要跑的任务（§7.3 按需裁剪）。"""
    if ARM_TASK_FILTER.get(arm) == "large_diff_only":
        return [t for t in tasks if is_large_diff(spec_of(t))]
    return list(tasks)


@dataclass
class Plan:
    """预检结果：跑什么、跑多少次、大概多久。"""
    entries: list[tuple[Any, str, int]]
    per_arm: dict[str, int]
    skipped: dict[str, int]

    @property
    def total(self) -> int:
        return len(self.entries)

    def runs_for(self, arm: str) -> int:
        return sum(1 for _t, a, _r in self.entries if a == arm)

    def estimate_minutes(self, minutes_per_run: float = DEFAULT_MINUTES_PER_RUN) -> float:
        return self.total * minutes_per_run

    def describe(self) -> str:
        lines = ["跑分预检（EVAL.md §7.1 矩阵）", "",
                 f"  {'arm':<9}{'任务数':>7}{'重复':>6}{'运行数':>8}   说明"]
        for arm in ARMS:
            if arm not in self.per_arm:
                continue
            n = self.per_arm[arm]
            runs = self.runs_for(arm)
            rep = runs // n if n else 0
            note = ARMS.get(arm, "")
            if self.skipped.get(arm):
                note += f"（裁剪掉 {self.skipped[arm]} 个小改动任务）"
            lines.append(f"  {arm:<9}{n:>7}{rep:>6}{runs:>8}   {note}")
        m = self.estimate_minutes()
        lines += ["", f"  合计 {self.total} 次运行；按 {DEFAULT_MINUTES_PER_RUN:g} 分钟/次 "
                      f"≈ {m / 60:.1f} 小时（{m / 60 / 24:.1f} 天）串行。",
                  "  这是必须提前规划的资源（§7.2）。--dry-run 可只看预检不执行。"]
        return "\n".join(lines)


def plan_matrix(tasks: Sequence[Any], arms: Sequence[str] = tuple(ARMS),
                runs: int = 3, *, runs_by_arm: dict[str, int] | None = None) -> Plan:
    """展开跑分矩阵并按 ARM_TASK_FILTER 裁剪。

    runs_by_arm 是给 Gate 0 用的（A 组只跑 1 次，§7.3 分层跑），
    避免为「只跑一次」再写一套矩阵逻辑。
    """
    entries: list[tuple[Any, str, int]] = []
    per_arm: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for arm in arms:
        sel = tasks_for_arm(arm, tasks)
        per_arm[arm] = len(sel)
        skipped[arm] = len(tasks) - len(sel)
        n_runs = (runs_by_arm or {}).get(arm, runs)
        for t in sel:
            for r in range(n_runs):
                entries.append((t, arm, r))
    return Plan(entries, per_arm, skipped)


def completed_keys(attempts: Iterable[Attempt]) -> set[tuple[str, str, int]]:
    """已完成的 (task_id, arm, run_index) 集合 —— 续跑的判据。"""
    return {(a.task_id, a.arm, a.run_index) for a in attempts}
