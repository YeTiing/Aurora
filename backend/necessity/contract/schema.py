"""契约候选的数据结构 —— 规范 §4.5。

## 核心洞察（规范 §4.3）

**不新建执行机制。** 挖出的契约编译成 `guard` 已有的 8 种类型之一，
于是拦截/回滚/账本全部白送。

所以本模块的关键字段是 `guard_type` + `guard_scope` —— 它们必须
**精确匹配** `guard/spec.py` 的 scope 约定，否则编译出的约束会被
`checker` 当成格式错误（而那不是报错，是静默不生效）。

## 置信度与证据强度（规范 §4.5 的表）

    3 星：测试断言 / 调用关系 / 历史修改
    2 星：异常处理 / 类型声明 / 相似实现
    1 星：运行时轨迹

置信度不是「模型觉得像」，而是**独立证据条数 × 来源强度**的确定性折算。
规范要求「≥0.8 且 ≥2 条独立来源」才自动注入，所以这个折算必须可复核。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# 证据来源的强度（规范 §4.5 表格右列）。
# ⚠️ 这张表是**判据**而不是装饰：置信度由它折算，而置信度决定
# 「自动注入」还是「进人工队列」。改这里的数字会直接改变注入行为。
SOURCE_STRENGTH: dict[str, float] = {
    # ⭐⭐⭐ 三个强来源：单独一条就接近门槛，配第二条即跨过 0.8
    "tests": 0.70,       # 测试断言：可执行的断言，最硬
    "callgraph": 0.70,   # 调用关系：被调用后必被另一函数调用
    "history": 0.70,     # 历史修改：被 revert / 带 fix 的提交
    # ⭐⭐ 中等来源：单条不够自动注入，需要叠加
    "exceptions": 0.55,
    "types": 0.50,
    "similar": 0.50,
    # ⭐ 弱来源：单独出现时不提（避免噪音）
    "trace": 0.30,
}

# 自动注入的门槛（规范 §4.7 的三档）
AUTO_INJECT_AT = 0.8      # ≥ 此值且 ≥2 条独立来源 -> 自动注入
REVIEW_AT = 0.5           # [REVIEW_AT, AUTO_INJECT_AT) -> 进人工队列
# < REVIEW_AT 不提（避免噪音）

# 自动注入还要求的最少独立来源数（规范 §4.7 表格「≥2 条独立来源」）
MIN_SOURCES_FOR_AUTO = 2


@dataclass
class ContractCandidate:
    """一条候选契约（规范 §4.5）。"""

    id: str = ""
    statement: str = ""                  # 自然语言描述
    sources: list[str] = field(default_factory=list)   # 证据来源（可多条）
    confidence: float = 0.0
    evidence_count: int = 0              # 独立证据条数
    guard_type: str = ""                 # 编译成的 guard 类型（8 选 1）
    guard_scope: dict = field(default_factory=dict)
    needs_review: bool = False           # 置信度低于阈值 -> 必须人工确认
    polluted_by_stale: bool = False      # 依赖过期索引（§1.5）

    @property
    def injection(self) -> str:
        """注入策略（规范 §4.7 三档）。"""
        if self.polluted_by_stale:
            # 规范 §1.5 + 门禁：「`polluted_by_stale` 的契约不注入」。
            # 宁可少一条契约，不可注入错的 —— 错的契约会拦住正确改动。
            return "skip_stale"
        if self.confidence >= AUTO_INJECT_AT and len(set(self.sources)) >= MIN_SOURCES_FOR_AUTO:
            return "auto"
        if self.confidence >= REVIEW_AT:
            return "review"
        return "drop"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "statement": self.statement,
            "sources": list(self.sources), "confidence": round(self.confidence, 4),
            "evidence_count": self.evidence_count,
            "guard_type": self.guard_type, "guard_scope": self.guard_scope,
            "needs_review": self.needs_review,
            "polluted_by_stale": self.polluted_by_stale,
            "injection": self.injection,
        }


def compute_confidence(sources: list[str], evidence_count: int = 1) -> float:
    """由证据来源折算置信度。

    规则（**按规范 §4.7 的门槛反推**，不是随便定的权重）：

      · 基数 = **最强来源**的强度。取 max 而不是求和 —— 求和会让
        「7 个弱来源」超过「1 个强来源」，而那不符合「独立来源」的本意。
      · **独立来源数**是主要加分项：每条额外来源 +0.15。
        规范的门槛写的是「强证据（≥2 条独立来源）」，所以「多个来源」
        才是从强到强的关键条件。
      · 证据条数每多一条 +0.05，上限 +0.15（多次出现说明稳定，
        但无界加分会让「出现 20 次」压过「来源多样」）。
      · 未知来源不计分（不给默认值 —— 未知来源的强度我们不知道）。

    ⚠️ 校准依据（实测踩过）：初版把基数压得太低（tests=0.45），
    于是**最高只能到 0.65**，永远够不到 §4.7 的 0.8 自动注入门槛 ——
    那一档形同虚设，所有契约都被丢进人工队列。
    现在的取值使「⭐⭐⭐ 来源 + ≥2 条独立来源」恰好 ≥0.8，
    与规范门槛一致；而「单条弱来源」仍是低分（<0.5，不提）。
    """
    known = [s for s in set(sources or []) if s in SOURCE_STRENGTH]
    if not known:
        return 0.0
    base = max(SOURCE_STRENGTH[s] for s in known)
    src_bonus = 0.15 * (len(known) - 1)
    ev_bonus = min(0.15, 0.05 * max(0, int(evidence_count) - 1))
    return round(min(1.0, base + src_bonus + ev_bonus), 4)


# ── 契约 → guard 约束的编译目标（规范 §4.3 的映射表）────────────

# 隐式约定 -> 编译成的 guard 类型。这张表是「执行层白送」的具体体现：
# 挖出的契约只要落进这 8 种之一，就直接接上现成的拦截/回滚。
CONTRACT_KINDS: dict[str, str] = {
    "api_compatible": "signature_stable",   # 公共接口必须向后兼容
    "soft_delete": "symbol_scope",          # 删除必须软删除
    "cache_invalidation": "call_chain",     # 配置更新后必须清缓存
    "bounded_impact": "impact_limit",       # 改动影响面不超过 N 文件
    "must_pass_tests": "test_preserved",    # 指定测试必须仍通过
}


@dataclass
class ContractSet:
    """一次挖掘的完整产出。"""

    candidates: list[ContractCandidate] = field(default_factory=list)
    # ground truth 评估（规范 §4.6 的 git 历史裁判法）
    recall_lower_bound: float | None = None
    false_positive_rate: float | None = None
    notes: list[str] = field(default_factory=list)

    def by_injection(self, mode: str) -> list[ContractCandidate]:
        return [c for c in self.candidates if c.injection == mode]

    @property
    def auto_injectable(self) -> list[ContractCandidate]:
        return self.by_injection("auto")

    def to_dict(self) -> dict:
        from collections import Counter
        return {
            "total": len(self.candidates),
            "by_injection": dict(Counter(c.injection for c in self.candidates)),
            "recall_lower_bound": self.recall_lower_bound,
            "false_positive_rate": self.false_positive_rate,
            "notes": list(self.notes),
            "candidates": [c.to_dict() for c in self.candidates],
        }


__all__ = [
    "AUTO_INJECT_AT", "CONTRACT_KINDS", "ContractCandidate", "ContractSet",
    "MIN_SOURCES_FOR_AUTO", "REVIEW_AT", "SOURCE_STRENGTH", "compute_confidence",
]
