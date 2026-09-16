"""信号 → 风险分 → 权限档 —— 规范 §5.4。

## 决策映射（规范 §5.4 原表）

    无高风险信号     -> auto        直接执行
    中等风险        -> plan_first  先生成计划与影响面，再执行
    高风险          -> ask         在关键决策点用针对性问题请求确认
    无法可靠判断    -> stop        停止修改，说明缺少什么

## 两个必须写进代码的判断

**一、未检测的信号按风险计，不按「无风险」计。**

`RiskSignals` 的每个字段是三态的，`None` = 未检测。
把它当 `False` 处理是这套系统里最危险的默认 —— 那等于说
「我不知道有没有风险，所以放行」。所以未检测按 **+1 分**计（与该信号
为真时同分），并记进 `unknown` 让用户看到「我是因为信息不足才谨慎的」。

**二、「需求歧义」只作加分项，不单独决定 ask。**

规范 §5.12 次要风险的缓解措施原话：
「该项只作为加分信号，不单独决定 ask」。
理由：它依赖 LLM 稳定性，判错时会直接打扰用户。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .signals import RiskSignals

Level = Literal["auto", "plan_first", "ask", "stop"]

# 分档阈值。规范给了四档但没给分值 —— 这里是**设计选择**，且刻意保守：
# 一档风险就足以进入 plan_first（先生成计划不会打扰用户，只是慢一点），
# 而 ask（真打扰用户）需要累计到 3 分。
PLAN_FIRST_AT = 1
ASK_AT = 3
# 「无法可靠判断」的判据。
#
# ⚠️ **不是**「未检测项多就算无法判断」。实测踩过：任务刚开始时符号、测试、
# 调用图都还没采集（5 项未检测），于是**每个任务**开局都判成 stop ——
# 包括「把 add 函数改成加法」这种毫无风险的需求。功能因此形同虚设。
#
# 「无法可靠判断」的语义是「**该拿到的信息拿不到**」，不是「还没开始采集」。
# 所以判据是：未检测项数 ≥ 阈值 **且** 没有任何一项确认的风险信号。
# 后者是关键 —— 一旦有确认的风险，说明至少判断在工作，
# 那就按分数走 ask/plan_first，而不是把用户丢进 stop。
STOP_UNKNOWN_AT = 7
# 信号总数（用于区分「全部未采集 = 开局」与「部分采集不到 = 真的判断不了」）
_ALL_SIGNALS = 8


@dataclass
class AutonomyDecision:
    """一次权限判定（规范 §5.4）。"""

    level: Level = "auto"
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    # 触发了哪些信号（便于报告与回归测试断言）
    triggered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"level": self.level, "score": self.score,
                "reasons": list(self.reasons), "questions": list(self.questions),
                "unknown": list(self.unknown), "triggered": list(self.triggered)}


# 每个信号的风险权重。名称 -> (分数, 人类可读原因)
#
# 权重理由（不是拍数，是「错了会怎样」的排序）：
#   2 分：改错了后果严重或不可逆（公共接口、回滚成本）
#   1 分：可控但值得放慢（范围、领域、测试缺失）
_WEIGHTS: dict[str, tuple[float, str]] = {
    "requirement_ambiguous": (1.0, "需求有多种合理解释"),
    "target_symbol_unique": (1.0, "无法唯一定位目标符号"),
    "callgraph_fresh": (1.0, "调用图不新鲜（影响面可能不完整）"),
    "touches_public_api": (2.0, "改动触及公共接口（可能破坏调用方）"),
    "has_related_tests": (1.0, "缺少关联测试"),
    "involves_risky_domain": (1.0, "涉及数据库/权限/安全/依赖升级"),
    "scope_over_budget": (1.0, "修改范围超出预算"),
    "rollback_costly": (2.0, "回滚成本高"),
}


def _triggered(name: str, value) -> bool:
    """该信号是否表示**有风险**。

    语义方向各字段不同：`target_symbol_unique=False` 才是风险，
    而 `requirement_ambiguous=True` 才是风险。所以逐个判，别用一个统一表达式。
    """
    if name in ("target_symbol_unique", "callgraph_fresh", "has_related_tests"):
        return value is False
    return value is True


def score_signals(s: RiskSignals, *, budgets=None) -> AutonomyDecision:
    """把信号打成风险分并映射到权限档。"""
    d = AutonomyDecision(unknown=list(s.unknown))

    # 先统计「确认有风险」与「未检测」各几项 —— 顺序很重要：
    # 判 stop 需要知道有没有任何一项是**确认**的（见 STOP_UNKNOWN_AT 的注释）。
    confirmed: list[tuple[str, float, str]] = []
    for name, (weight, why) in _WEIGHTS.items():
        v = getattr(s, name, None)
        if v is None:
            continue
        if _triggered(name, v):
            confirmed.append((name, weight, why))

    # 「无法可靠判断」：信息大面积缺失**且**没有任何确认信号可依据。
    # 若已有确认信号，说明判断在工作 —— 那就按分数走 ask/plan_first，
    # 而不是把用户丢进 stop（那会让「停止」失去信号量）。
    #
    # ⚠️ 但「**全部**未检测」不等于「无法可靠判断」—— 那是「还没开始采集」，
    # 是任务开局的**正常**状态（符号/调用图/测试历史都要等 Agent 动手才有）。
    # 规范 §5.12 给的缓解措施正是「**先跑 plan_first** 收集阶段 A 数据，
    # 再逐步收紧」—— 所以开局应当是 plan_first，不是 stop。
    # 实测踩过：判成 stop 会让每个任务在开局就停住，功能形同虚设。
    if s.unknown_count >= STOP_UNKNOWN_AT and not confirmed:
        if s.unknown_count >= _ALL_SIGNALS:
            d.level = "plan_first"
            d.score = float(s.unknown_count)
            d.reasons.append(
                f"全部 {s.unknown_count} 项风险信号尚未采集（任务开局）—— "
                "先生成计划与影响面，等信息补齐后再决定是否需要确认")
            return d
        d.level = "stop"
        d.score = float(s.unknown_count)
        d.reasons.append(
            f"{s.unknown_count} 项风险信号未能采集，且没有任何一项可确认 —— "
            "无法可靠判断风险，不自行决定；请补齐信息（或显式指定权限策略）")
        return d

    # 未检测项按**有风险**计分，并显式说明。把它当 False 等于
    # 「不知道有没有风险所以放行」—— 本系统最危险的默认。
    for name, (weight, why) in _WEIGHTS.items():
        if getattr(s, name, None) is None:
            d.score += weight
            d.triggered.append(f"{name}(未检测)")
            d.reasons.append(f"{why} —— **未检测**（按风险计，不按无风险计）")
    for name, weight, why in confirmed:
        d.score += weight
        d.triggered.append(name)
        d.reasons.append(why)

    # A2 的自动契约数作为加分信号（规范 §1.6 交互③）：
    #   契约越多说明这块代码的隐式约定越密集，改错的代价越高。
    if s.active_contracts >= 6:
        d.score += 2.0
        d.triggered.append("active_contracts>=6")
        d.reasons.append(f"生效的自动契约 {s.active_contracts} 条 —— 隐式约定密集")
    elif s.active_contracts >= 3:
        d.score += 1.0
        d.triggered.append("active_contracts>=3")
        d.reasons.append(f"生效的自动契约 {s.active_contracts} 条")

    d.score = round(d.score, 2)
    if d.score >= ASK_AT:
        d.level = "ask"
    elif d.score >= PLAN_FIRST_AT:
        d.level = "plan_first"
    else:
        d.level = "auto"
    return d


# ── 权限档 → 审批策略（规范 §5.10：A3 是决策层，不替代审批系统）──

# 映射到 `approval.py` 已有的 4 档。**不新增档位** ——
# 规范 §5.10 原话：「不改变审批系统本身，回滚无残留」。
_APPROVAL_MAP = {
    "auto": "never",          # 直接执行
    "plan_first": "never",    # 也直接执行，但要先出计划（由编排层负责）
    "ask": "on-request",      # 关键决策点请求确认
    "stop": "untrusted",      # 停止修改，事事确认
}


def to_approval_policy(level: Level) -> str:
    """权限档 → `approval.py` 的策略名。

    `plan_first` 刻意映射为 `never`：它要求「先生成计划」是**编排层**的动作，
    不是审批层的动作。混在一起会让「出计划」变成「问用户要不要出计划」，
    那是多余的一问（规范 §5.5：不要问「是否继续」）。
    """
    return _APPROVAL_MAP.get(level, "on-request")


__all__ = [
    "ASK_AT", "AutonomyDecision", "Level", "PLAN_FIRST_AT", "STOP_UNKNOWN_AT",
    "score_signals", "to_approval_policy",
]
