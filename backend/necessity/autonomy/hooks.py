"""A3 的钩子装配 —— 规范 §5。

## A3 是**决策层**，不是拦截层

规范 §5.10 原话：「A3 是决策层，关闭后退回固定的 `ApprovalPolicy`。
**不改变审批系统本身**，回滚无残留」。

所以本类**不自己拦截工具调用** —— 它做的是：
    1. `on_task_start` 时采集信号、算出权限档、把决策存起来
    2. 暴露 `decision` 供编排层读取（何时该问、该问什么）
    3. 把决策记进轨迹（供 §1.4 的闭环观测 `autonomy_asks` 等字段）

真正的「问」由宿主的审批系统执行 —— A3 只决定「该不该问」与「问什么」。
这条边界很重要：若 A3 自己弹问题，关闭它就会留下「问了一半」的状态。

## 需求歧义从哪来（规范 §5.6 的关键约束）

**零新增 LLM 调用点。** `on_task_start` 在主循环路径上，hooks 契约
（§1.2 契约 3）禁止在那里调 LLM。所以：
    - `guard.compiler` 编译约束时顺带产出 `ambiguous`（它本来就允许调 LLM）
    - A3 只**消费**那个字段，不自己判定

compiler 未启用时 `requirement_ambiguous` 标为**未检测**而非 False
（§5.6 原话：「标注为『未检测』而非『无歧义』」）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from backend.necessity.hooks import TaskResult

from .clarify import Question, generate
from .score import AutonomyDecision, score_signals, to_approval_policy
from .signals import RiskSignals, collect

logger = logging.getLogger("necessity.autonomy.hooks")


@dataclass
class AutonomyState:
    """一个任务的权限判定状态。"""

    decision: AutonomyDecision = field(default_factory=AutonomyDecision)
    signals: RiskSignals = field(default_factory=RiskSignals)
    questions: list[Question] = field(default_factory=list)
    # 是否已被用户推翻（§1.4 的观测字段 autonomy_overridden）
    overridden: bool = False

    @property
    def policy(self) -> str:
        """对应的审批策略名（映射到 `approval.py` 的已有档位）。"""
        return to_approval_policy(self.decision.level)


class AutonomyHooks:
    """A3 不确定性感知执行。"""

    name = "autonomy"

    def __init__(self, *, workspace: str = ".", file_budget: int = 5,
                 enabled_for_levels=("ask", "stop")) -> None:
        self.workspace = str(workspace or ".")
        self.file_budget = int(file_budget)
        # 只在哪些档位产出问题。`auto` / `plan_first` 不问 ——
        # plan_first 的动作是「先出计划」，不是「问用户」
        # （规范 §5.5：不要问「是否继续」）。
        self.enabled_for_levels = tuple(enabled_for_levels)
        self.state = AutonomyState()
        self._task_text = ""

    # ── 钩子 ─────────────────────────────────────────────────────

    def on_task_start(self, task: dict) -> None:
        """任务开始：采集信号 → 打分 → 生成问题。**不调 LLM。**

        ⚠️ 这里拿不到改动符号（还没动手），所以 `target_symbol_unique` /
        `touches_public_api` / `has_related_tests` 多为未检测。
        那是有意的：**开局就按最保守计分**，随着信息补齐（`refresh()`）
        再放宽。反过来（开局宽松、事后收紧）会让已经执行的改动无法撤销。
        """
        task = task or {}
        self._task_text = str(task.get("input") or task.get("task") or "")
        self.refresh()

    def refresh(self, **kwargs) -> AutonomyDecision:
        """重新采集与打分。编排层在拿到新信息（影响面/符号/测试）后调用。"""
        signals = collect(
            self._task_text, workspace=self.workspace,
            file_budget=self.file_budget, **kwargs)
        decision = score_signals(signals)
        self.state = AutonomyState(decision=decision, signals=signals)

        if decision.level in self.enabled_for_levels:
            self.state.questions = generate(signals)
            if not self.state.questions and decision.level == "ask":
                # 该问却问不出来 —— 那是**问题库覆盖不足**，不是「无需确认」。
                # 如实记下来，并降级为 plan_first（先出计划让用户看）。
                logger.warning(
                    "判定为 ask 但未能生成合规问题（问题库未覆盖触发的信号：%s）"
                    "—— 降级为 plan_first", decision.triggered)
                self.state.decision = AutonomyDecision(
                    level="plan_first", score=decision.score,
                    reasons=decision.reasons + ["无可用澄清问题，改为先出计划"],
                    unknown=decision.unknown, triggered=decision.triggered)
        return self.state.decision

    def on_task_end(self, result: TaskResult) -> dict:
        """产出观测指标（规范 §1.4 表格里 A3 的三个字段）。"""
        st = self.state
        return {
            "autonomy_level": st.decision.level,
            "autonomy_policy": st.policy,
            "autonomy_asks": 1 if st.decision.level == "ask" else 0,
            "autonomy_overridden": 1 if st.overridden else 0,
            "autonomy_missed_risk": 0.0,   # 由「实际出事但判 auto」回填
            "autonomy_score": st.decision.score,
            "autonomy_unknown": st.signals.unknown_count,
            "autonomy_questions": [q.to_dict() for q in st.questions],
        }

    # ── 供编排层读取 ─────────────────────────────────────────────

    @property
    def decision(self) -> AutonomyDecision:
        return self.state.decision

    @property
    def questions(self) -> list[Question]:
        return list(self.state.questions)

    def mark_overridden(self) -> None:
        """用户推翻了本档位（选了更宽松的执行）。记进闭环观测。"""
        self.state.overridden = True


def build_autonomy_hooks(cfg: dict | None = None) -> AutonomyHooks:
    """`load_capabilities` 约定的工厂（`build_<name>_hooks`）。"""
    cfg = cfg or {}
    return AutonomyHooks(
        workspace=str(cfg.get("workspace", ".") or "."),
        file_budget=int(cfg.get("file_budget", 5) or 5),
    )


__all__ = ["AutonomyHooks", "AutonomyState", "build_autonomy_hooks"]
