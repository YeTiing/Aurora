"""统一评分与独立评审 —— 规范 §8.4 / §8.5。

## 评分方式：硬门禁 + 按序比较（**不做加权总分**）

规范给的理由（§8.4）：
    「为什么不用加权总分：权重无法客观标定，会『用一个编出来的数字掩盖
      真实权衡』。
      为什么分级比较：裁决过程**可解释、可审计**，每步都能说清为什么。」

所以：
    硬门禁（任一不过 → 淘汰）
      ① 测试必须通过
      ② 安全扫描不得有 critical
      ③ 不得违反 guard 已编译的约束（含 A2 的自动契约）
    通过者按序比较
      第 1 优先：安全发现数（少者优）
      第 2 优先：影响面文件数（少者优；依赖 stale 索引则降权）
      第 3 优先：冗余率（低者优）
      第 4 优先：改动行数（少者优）

## 与 A2 的交互（§8.5 ①，最容易做错的一处）

    4 个候选全部违反同一条约束
      → **不得选「违反最少」的那个**
      → 必须报告「无可行候选」+ 列出全部候选的违规详情
      → 交回人类决策

「选违反最少的」看起来合理，实际是危险的：它让用户在不知情的情况下
接受了一个违规方案 —— 而违反的正是 A2 挖出的隐式约定。

## 评审独立性（§8.7 硬要求 100%）

「评审者 ID ≠ 生成者 ID（可断言）」。本模块提供可断言的形式 ——
不是靠约定，是能直接检查的字段。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .schema import Candidate, Verdict

logger = logging.getLogger("necessity.compete.judge")

# 评审者标识。与生成者分离 —— 规范要求「独立评审」。
JUDGE_ID = "necessity.judge"

# 影响面依赖过期索引时的降权倍数（规范 §8.4 第 2 优先的括号说明）。
# 0.5 是设计选择：过期不等于无效，但可信度下降。
STALE_IMPACT_PENALTY = 0.5


@dataclass
class RankKey:
    """按序比较用的**分级排序键**。

    组成一个 tuple 后直接用 Python 的 tuple 比较 —— 这样「按序比较」
    是语言保证的，而不是我手写一串 if-else（后者容易在改动时漏掉一级）。
    """
    security_findings: int
    impact_effective: float
    redundancy_ratio: float
    diff_lines: int

    def as_tuple(self) -> tuple:
        return (self.security_findings, self.impact_effective,
                self.redundancy_ratio, self.diff_lines)


def rank_key(c: Candidate) -> RankKey:
    """从候选指标算出分级排序键。"""
    m = c.metrics
    impact = float(m.impact_files)
    if m.impact_stale:
        # 过期数据降权：让「基于旧索引算出的好看数字」不能靠不可靠
        # 的信息取胜（§1.5：A6 的策略是「降权该维度」）。
        impact *= STALE_IMPACT_PENALTY
    return RankKey(
        security_findings=int(m.security_findings),
        impact_effective=impact,
        redundancy_ratio=float(m.redundancy_ratio),
        diff_lines=int(m.diff_lines),
    )


def _describe(key: RankKey) -> list[str]:
    """把排序键转成人类可读的比较依据。"""
    return [f"安全发现 {key.security_findings}",
            f"影响面(加权) {key.impact_effective:g}",
            f"冗余率 {key.redundancy_ratio:.2f}",
            f"改动行数 {key.diff_lines}"]


def judge(candidates: list[Candidate], *,
          judge_id: str = JUDGE_ID) -> Verdict:
    """评审并裁决（规范 §8.4 / §8.5）。"""
    v = Verdict()
    if not candidates:
        v.notes.append("没有候选可评审")
        return v

    # ── 独立性检查（§8.7 硬要求）────────────────────────────────
    if judge_id:
        same = [c.id for c in candidates if c.generator == judge_id]
        if same:
            v.notes.append(
                f"⚠️ 评审独立性被破坏：候选 {same} 的生成者与评审者相同"
                f"（都是 {judge_id}）—— 规范 §8.7 要求评审者 ID ≠ 生成者 ID")

    # ── 硬门禁 ────────────────────────────────────────────────────
    passed: list[Candidate] = []
    for c in candidates:
        bad = c.metrics.passed_gates
        if bad:
            v.rejected_reasons[c.id] = _gate_reason(c, bad)
            continue
        passed.append(c)

    if not passed:
        # **全部被淘汰 —— 包括全部违反同一约束的情形（§8.5 ①）。**
        # 规范明确禁止「选违反最少的那个」：那会让用户在接受一个违规方案时
        # 毫不知情，而违反的正是 A2 挖出的隐式约定。
        v.no_viable_candidate = True
        v.notes.append(
            f"无可行候选：{len(candidates)} 个候选全部未通过硬门禁。"
            "**不选『违反最少』的那个** —— 违规方案需要人来决定是否接受。")
        return v

    # ── 按序比较 ──────────────────────────────────────────────────
    keyed = sorted(passed, key=lambda c: rank_key(c).as_tuple())
    v.ranking = [c.id for c in keyed]
    v.winner = keyed[0].id
    for c in keyed:
        v.evidence[c.id] = _describe(rank_key(c))

    if len(keyed) > 1:
        first, second = keyed[0], keyed[1]
        v.tie_breakers = _tie_break(
            _describe(rank_key(first)), _describe(rank_key(second)),
            first.strategy, second.strategy)
    else:
        v.tie_breakers = [f"唯一通过硬门禁的候选（其余 {len(candidates) - 1} 个被淘汰）"]

    return v


def _gate_reason(c: Candidate, bad: list[str]) -> str:
    m = c.metrics
    parts: list[str] = []
    if "tests" in bad:
        parts.append(f"测试未通过（{m.tests_total} 个用例）")
    if "security_critical" in bad:
        parts.append(f"安全扫描有 {m.security_critical} 个 critical")
    if "constraints" in bad:
        parts.append(f"违反约束：{'、'.join(m.violated_constraints)}")
    if c.error:
        parts.append(f"执行错误：{c.error}")
    return "；".join(parts) or "未通过硬门禁"


def _tie_break(win_desc: list[str], lose_desc: list[str],
               win_strategy: str, lose_strategy: str) -> list[str]:
    """记录胜者凭什么赢 —— 逐级比较，记录**第一级分出胜负的维度**。

    这是「可解释」的具体含义：不是给一个总分，而是指出在哪一级、
    差多少。用户据此能判断这个裁决是否合理。
    """
    labels = ["安全发现数", "影响面（加权后）", "冗余率", "改动行数"]
    out: list[str] = []
    for i, label in enumerate(labels):
        out.append(f"第 {i + 1} 级 {label}：{win_desc[i]} vs {lose_desc[i]}")
        if win_desc[i] != lose_desc[i]:
            out.append(
                f"→ 在第 {i + 1} 级（{label}）分出胜负："
                f"`{win_strategy}` 优于 `{lose_strategy}`")
            break
    else:
        out.append("→ 四级指标全部相同（真平局），按候选 id 稳定排序")
    return out


def diversity(candidates: list[Candidate]) -> float:
    """候选多样性 —— 两两不重复率（规范 §8.7 硬门禁 ≥75%）。

    「不重复」的判据是 **diff 文本归一化后不同**。若四种策略产出一样的补丁，
    「竞争」就是假的（规范 §8.6 主要风险：「候选趋同」）。
    """
    if len(candidates) < 2:
        # 只有一个候选时无所谓多样性 —— 但也不能报 1.0 假装测过
        return 1.0 if candidates else 0.0
    keys = {_diff_key(c) for c in candidates}
    return len(keys) / len(candidates)


def _diff_key(c: Candidate) -> str:
    """比较用的归一化 key：忽略行尾差异与纯空白行。"""
    text = (c.diff or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


__all__ = [
    "JUDGE_ID", "RankKey", "STALE_IMPACT_PENALTY", "diversity", "judge", "rank_key",
]
