"""多方案竞争的数据结构 —— 规范 §8.4。

## 重点不在「同时启动多个 Agent」

规范把这句话放在问题定义里（§8.1）：
    「重点不在『同时启动多个 Agent』，而在：多个候选基于**统一指标**竞争，
      由**独立验证结果**决定最终方案。」

所以数据结构围绕两件事设计：**统一指标**（`CandidateMetrics`）与
**可解释的裁决**（`Verdict.tie_breakers` / `rejected_reasons`）。

## 为什么不用加权总分（规范 §8.4 的原话）

    「权重无法客观标定，会『用一个编出来的数字掩盖真实权衡』」

所以评分是**硬门禁 + 按序比较**：先淘汰不合格的，再按优先级逐级比较。
这样每一步都能说清「为什么它赢了」，而不是给一个 0.73 分的黑箱。

## 四种策略为什么必须有区分度（§8.4）

`minimal` / `backward_compatible` / `structural` / `security_first`
各自施加**不同约束**，期望产出不同的补丁。若四种策略产出一样，
「竞争」就是假的 —— 规范把**多样性**列为硬门禁（两两不重复率 ≥75%）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Strategy = Literal[
    "minimal", "backward_compatible", "structural", "security_first",
]

# 四种策略及其约束（规范 §8.4 的表格）。
# 这张表就是「如何产出有区分度的方案」的答案 —— 不是换 prompt 措辞，
# 而是施加**不同的硬约束**。约束不同，产出才可能真的不同。
STRATEGIES: dict[str, dict] = {
    "minimal": {
        "constraint": "只做最小必要改动",
        "expect": "改动行数最少",
    },
    "backward_compatible": {
        "constraint": "公共接口签名不变",
        "expect": "无破坏性变更",
    },
    "structural": {
        "constraint": "允许重构",
        "expect": "影响面可能更大但更一致",
    },
    "security_first": {
        "constraint": "不新增依赖、不扩大权限",
        "expect": "安全扫描零发现",
    },
}

# 多样性硬门禁（规范 §8.7）：两两不重复率低于这个值说明策略没起作用
MIN_DIVERSITY = 0.75


@dataclass
class CandidateMetrics:
    """一个候选的**统一指标**（规范 §8.4）。

    所有候选必须用同一组指标度量 —— 否则「比较」无从谈起。
    """
    tests_pass: bool = False
    tests_total: int = 0
    impact_files: int = 0
    impact_symbols: int = 0
    # ★ §1.5：影响面依赖过期索引时，该维度可信度下降
    impact_stale: bool = False
    security_findings: int = 0
    security_critical: int = 0
    redundancy_ratio: float = 0.0
    diff_lines: int = 0
    # 违反了哪些已编译约束（规范 §8.4 硬门禁③，含 A2 的自动契约）
    violated_constraints: list[str] = field(default_factory=list)

    @property
    def passed_gates(self) -> list[str]:
        """未通过的硬门禁名（空 = 全过）。"""
        bad: list[str] = []
        if not self.tests_pass:
            bad.append("tests")
        if self.security_critical > 0:
            bad.append("security_critical")
        if self.violated_constraints:
            bad.append("constraints")
        return bad

    def to_dict(self) -> dict:
        return {
            "tests_pass": self.tests_pass, "tests_total": self.tests_total,
            "impact_files": self.impact_files, "impact_symbols": self.impact_symbols,
            "impact_stale": self.impact_stale,
            "security_findings": self.security_findings,
            "security_critical": self.security_critical,
            "redundancy_ratio": round(self.redundancy_ratio, 4),
            "diff_lines": self.diff_lines,
            "violated_constraints": list(self.violated_constraints),
        }


@dataclass
class Candidate:
    """一个候选方案（规范 §8.4）。"""

    id: str = ""
    strategy: str = ""
    branch: str = ""              # 隔离分支/worktree 标识
    diff: str = ""
    metrics: CandidateMetrics = field(default_factory=CandidateMetrics)
    # 生成者 id（规范 §8.7：评审独立性要求评审者 ID ≠ 生成者 ID）
    generator: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "strategy": self.strategy, "branch": self.branch,
                "diff": self.diff, "metrics": self.metrics.to_dict(),
                "generator": self.generator, "error": self.error}


@dataclass
class Verdict:
    """裁决结果（规范 §8.4）。**可解释、可审计**是硬要求。"""

    winner: str = ""
    ranking: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    # 平局裁决依据 —— **必须记录**（§8.7：无 tie_breakers 的裁决不可发布）
    tie_breakers: list[str] = field(default_factory=list)
    # 失败候选的淘汰原因（§8.4 要求给出，便于用户理解为什么少了几个方案）
    rejected_reasons: dict = field(default_factory=dict)
    # 「无可行候选」（全部违反同一约束）—— 交回人类决策
    no_viable_candidate: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def explainable(self) -> bool:
        """裁决是否可解释（规范 §8.7 硬要求 100%）。"""
        if self.no_viable_candidate:
            # 「无可行候选」也是可解释的裁决 —— 它必须列出全部违规详情
            return bool(self.rejected_reasons)
        if not self.winner:
            return False
        # 有胜者时，要么有 tie_breakers 说明凭什么赢，要么它是唯一通过者
        return bool(self.tie_breakers) or len(self.ranking) == 1

    def to_dict(self) -> dict:
        return {
            "winner": self.winner, "ranking": list(self.ranking),
            "evidence": self.evidence, "tie_breakers": list(self.tie_breakers),
            "rejected_reasons": self.rejected_reasons,
            "no_viable_candidate": self.no_viable_candidate,
            "explainable": self.explainable, "notes": list(self.notes),
        }


__all__ = [
    "Candidate", "CandidateMetrics", "MIN_DIVERSITY", "STRATEGIES", "Strategy",
    "Verdict",
]
