"""Gate 指标数据模型 —— `GateMetrics`。

从 `records.py` 拆出（该文件触 300 行上限）。职责上也该分：
    gate_metrics.py 放**每次运行的指标**（Gate 0-6 + 六项能力的观测字段）
    records.py      放运行记录（Attempt）与 JSONL 读写

两者寿命不同：Attempt 的字段跟着跑分流程走，而指标字段会随着
六项能力逐个接入而增长（规范 §1.4 已预留 15 个字段）。
"""
from __future__ import annotations

from dataclasses import dataclass


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

    # ── 六项能力（规范 §1.4 的观测字段）──────────────────────────
    # 归属说明：这些字段由**各能力自己**填充，runtime_gate 只消费。
    # 全部给默认值 —— 旧 JSONL 记录里没有这些键，`from_dict` 取默认值即可，
    # 历史评测数据不能因为 schema 扩展而读不出来。
    # A1 可验证补丁
    bundle_fields_filled: float = 0.0
    bundle_impact_accuracy: float = 0.0
    # A2 契约挖掘
    contracts_active: int = 0
    contract_violations: int = 0
    contract_false_blocks: int = 0
    # A3 不确定性执行
    autonomy_asks: int = 0
    autonomy_overridden: int = 0
    autonomy_missed_risk: float = 0.0
    # A4 供应链安全
    admissions_checked: int = 0
    admissions_rejected: int = 0
    runtime_audit_events: int = 0
    # A5 Skill 评测
    skill_effect_size: float = 0.0
    skill_p_value: float = 0.0
    # A6 多方案竞争
    candidate_diversity: float = 0.0
    winner_vs_random_p: float = 1.0

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
            # 六项能力的观测字段（规范 §1.4）。全部写出 ——
            # 报告与 runtime_gate 都按名字读，拼错会静默失效。
            "bundle_fields_filled": self.bundle_fields_filled,
            "bundle_impact_accuracy": self.bundle_impact_accuracy,
            "contracts_active": self.contracts_active,
            "contract_violations": self.contract_violations,
            "contract_false_blocks": self.contract_false_blocks,
            "autonomy_asks": self.autonomy_asks,
            "autonomy_overridden": self.autonomy_overridden,
            "autonomy_missed_risk": self.autonomy_missed_risk,
            "admissions_checked": self.admissions_checked,
            "admissions_rejected": self.admissions_rejected,
            "runtime_audit_events": self.runtime_audit_events,
            "skill_effect_size": self.skill_effect_size,
            "skill_p_value": self.skill_p_value,
            "candidate_diversity": self.candidate_diversity,
            "winner_vs_random_p": self.winner_vs_random_p,
        }
        return d
