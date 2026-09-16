"""A3 不确定性感知执行 —— 规范 §5。

把「固定权限」变成「按风险动态授权 + 精准提问」。

## 结构

    signals.py  9 类风险信号采集（三态：True/False/未检测）
    score.py    信号 → 风险分 → 四档权限（auto/plan_first/ask/stop）
    clarify.py  生成「A 还是 B」式澄清问题（A3 的核心价值，§5.5）
    hooks.py    装配（走 build_autonomy_hooks 约定）

## 两条边界（规范明文规定，别越界）

1. **不替代审批系统**（§5.10）：A3 只决定「何时该问」，真正的「问」
   由宿主的 `approval.py` 执行。A3 自己弹问题会让回滚留下「问了一半」的状态。
2. **零新增 LLM 调用点**（§5.6）：需求歧义来自 `guard/compiler` 的副产品，
   A3 只消费。hooks 契约禁止在主循环调 LLM。

⚠️ 子模块显式 import：本仓库 `__init__.py` 的约定是列出子模块，
不列会让 `from backend.necessity.autonomy import signals` 失败（踩过两次）。
"""
from . import clarify, hooks, score, signals
from .clarify import MAX_OPTIONS, Question, generate, validate
from .hooks import AutonomyHooks, AutonomyState, build_autonomy_hooks
from .score import (
    ASK_AT,
    PLAN_FIRST_AT,
    STOP_UNKNOWN_AT,
    AutonomyDecision,
    Level,
    score_signals,
    to_approval_policy,
)
from .signals import RiskSignals, collect

__all__ = [
    # 子模块
    "clarify", "hooks", "score", "signals",
    # §5.5 澄清问题
    "MAX_OPTIONS", "Question", "generate", "validate",
    # §5 装配
    "AutonomyHooks", "AutonomyState", "build_autonomy_hooks",
    # §5.4 打分
    "ASK_AT", "PLAN_FIRST_AT", "STOP_UNKNOWN_AT", "AutonomyDecision", "Level",
    "score_signals", "to_approval_policy",
    # §5.4 信号
    "RiskSignals", "collect",
]
