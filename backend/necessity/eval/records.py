"""评测数据模型 —— runner / report / tasks 之间的**唯一契约**。

对应 EVAL.md §7.4「数据完整性要求」：

    attempt_id, task_id, config, run_index,
    start/end time, turns, tokens,
    all agent_events, final diff,
    test result, gate metrics
    **缺了轨迹就无法归因，必须重跑。**

为什么这个文件必须先定：
    runner（产出记录）、report（汇总指标）、tasks（校验任务）是三方独立的
    模块。若不先统一记录格式，各自会发明自己的字段名 —— 上一轮的符号 id
    冲突就是这么来的（两套方案导致整张图无法关联）。

本模块只定义**数据形状与序列化**，不含任何跑分逻辑。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

# ── 枚举取值（用字面量而非 Enum：JSONL 里可读，且跨版本稳定）────────

Status = Literal["pass", "fail", "error", "timeout", "skipped"]

# 对照组配置（EVAL.md §3.1 的两级对照）
#   A 裸 / B +Context / C +Guard / D +Reducer / E 全部
#   A′ 只在提示词里叮嘱（**最关键**：它代表现状做法）
#   B′ 无符号索引 / C′ 文本层面检查
ARMS: dict[str, str] = {
    "A": "裸 Agent",
    "B": "A + Context Paging",
    "C": "A + Constraint Guard",
    "D": "A + Diff Reducer",
    "E": "全部启用",
    "A_prime": "A + 提示词叮嘱（现状做法对照）",
    "B_prime": "A + 文件状态表但无符号索引",
    "C_prime": "A + 文本层面约束检查",
}

# 按需裁剪（EVAL.md §3.3：D 组只跑改动量大的任务，省算力）
ARM_TASK_FILTER: dict[str, str] = {
    "D": "large_diff_only",
}


@dataclass
class GateMetrics:
    """每个 attempt 都要记录的 gate 指标（EVAL.md §4 各 gate 的输入）。

    分开存而不是塞进 dict：这些字段是 gate 判据的直接来源，
    拼错名字会让 gate 静默失效。
    """
    # Gate 0：重复读取率
    reads_total: int = 0
    reads_waste: int = 0
    # Gate 2：Context Paging
    tokens_total: int = 0
    compaction_count: int = 0
    # Gate 3：影响面可用性
    impact_p95_ms: float = 0.0
    # Gate 4：Diff Reducer
    redundancy_ratio: float = 0.0
    reduce_converged: bool = False
    # Gate 5：Guard
    constraint_rho: float = 0.0          # 约束保持率（归一化，跨任务可比）
    constraint_survivals: int = -1       # 存活轮数 s（绝对，辅助展示），-1=无约束
    constraint_violations: int = 0
    # Gate 6：Attribution
    attribution_primary: str = ""
    attribution_unknown: bool = False

    @property
    def waste_ratio(self) -> float:
        """R_waste / R（EVAL.md §2.4 的定义）。"""
        return (self.reads_waste / self.reads_total) if self.reads_total else 0.0

    def to_dict(self) -> dict:
        d = {
            "reads_total": self.reads_total,
            "reads_waste": self.reads_waste,
            "waste_ratio": round(self.waste_ratio, 6),
            "tokens_total": self.tokens_total,
            "compaction_count": self.compaction_count,
            "impact_p95_ms": self.impact_p95_ms,
            "redundancy_ratio": self.redundancy_ratio,
            "reduce_converged": self.reduce_converged,
            "constraint_rho": self.constraint_rho,
            "constraint_survivals": self.constraint_survivals,
            "constraint_violations": self.constraint_violations,
            "attribution_primary": self.attribution_primary,
            "attribution_unknown": self.attribution_unknown,
        }
        return d


@dataclass
class Attempt:
    """一次「任务 × 对照组 × 重复轮次」的运行记录。

    EVAL.md §6.2 要求每组每任务跑 3 次取中位数 —— 所以 run_index 是必须的，
    单次结果不可信（LLM 采样非确定性）。
    """
    attempt_id: str = ""
    task_id: str = ""
    arm: str = ""                    # A / B / C / D / E / A_prime / ...
    run_index: int = 0
    category: str = ""               # A/B/C 任务类别（EVAL §1.4）

    started_at: float = 0.0
    ended_at: float = 0.0

    status: Status = "skipped"
    turns: int = 0
    tokens: int = 0

    diff_text: str = ""
    diff_stats: dict = field(default_factory=dict)

    # 轨迹必须全量保存：缺了无法归因（EVAL §7.4）
    events: list[dict] = field(default_factory=list)

    gates: GateMetrics = field(default_factory=GateMetrics)
    error: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "arm": self.arm,
            "run_index": self.run_index,
            "category": self.category,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_sec": round(self.duration_sec, 3),
            "status": self.status,
            "turns": self.turns,
            "tokens": self.tokens,
            "diff_text": self.diff_text,
            "diff_stats": self.diff_stats,
            "events": self.events,
            "gates": self.gates.to_dict(),
            "error": self.error,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Attempt":
        g = d.get("gates") or {}
        gm = GateMetrics(
            reads_total=int(g.get("reads_total", 0)),
            reads_waste=int(g.get("reads_waste", 0)),
            tokens_total=int(g.get("tokens_total", 0)),
            compaction_count=int(g.get("compaction_count", 0)),
            impact_p95_ms=float(g.get("impact_p95_ms", 0.0)),
            redundancy_ratio=float(g.get("redundancy_ratio", 0.0)),
            reduce_converged=bool(g.get("reduce_converged", False)),
            constraint_rho=float(g.get("constraint_rho", 0.0)),
            constraint_survivals=int(g.get("constraint_survivals", -1)),
            constraint_violations=int(g.get("constraint_violations", 0)),
            attribution_primary=str(g.get("attribution_primary", "")),
            attribution_unknown=bool(g.get("attribution_unknown", False)),
        )
        return cls(
            attempt_id=d.get("attempt_id", ""),
            task_id=d.get("task_id", ""),
            arm=d.get("arm", ""),
            run_index=int(d.get("run_index", 0)),
            category=d.get("category", ""),
            started_at=float(d.get("started_at", 0.0)),
            ended_at=float(d.get("ended_at", 0.0)),
            status=d.get("status", "skipped"),
            turns=int(d.get("turns", 0)),
            tokens=int(d.get("tokens", 0)),
            diff_text=d.get("diff_text", ""),
            diff_stats=d.get("diff_stats") or {},
            events=d.get("events") or [],
            gates=gm,
            error=d.get("error", ""),
            meta=d.get("meta") or {},
        )


# TaskSpec 已拆到 task_spec.py（records.py 只放运行记录）。
from .task_spec import TaskSpec  # noqa: E402,F401


# ── JSONL 读写（runner 产出 / report 消费）────────────────────────

def write_attempts(path: str | Path, attempts: Iterable[Attempt]) -> int:
    """追加写 JSONL。

    用 JSONL 而非大 JSON：跑分是长任务，中途崩溃时已完成的记录必须可用
    （EVAL §7.4：缺轨迹就得重跑，所以增量落盘是必须的）。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("a", encoding="utf-8") as f:
        for a in attempts:
            f.write(json.dumps(a.to_dict(), ensure_ascii=False) + "\n")
            n += 1
    return n


def read_attempts(path: str | Path) -> list[Attempt]:
    """读回 JSONL。坏行跳过并计数（不让一行损坏毁掉整个评测结果）。"""
    p = Path(path)
    if not p.exists():
        return []
    out: list[Attempt] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Attempt.from_dict(json.loads(line)))
        except Exception:
            continue
    return out


def make_attempt_id(task_id: str, arm: str, run_index: int) -> str:
    """`{task}#{arm}#{run}` —— 稳定且可读，便于日志里定位。"""
    return f"{task_id}#{arm}#{run_index}"


def now() -> float:
    return time.time()
