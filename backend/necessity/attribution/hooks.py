"""Failure Attribution 的钩子装配 + 离线分析入口。

为什么归因可以在钩子层做：
    它是**离线分析**（INTEGRATION.md §8.1「Attribution 默认关闭」），
    不在主循环路径上。唯一真正干活的钩子是 `on_task_end` —— 其余全部
    是空操作，符合 INTEGRATION.md §1.2「非侵入式」与契约 3
    「钩子不得调用 LLM（除 classifier）」：classifier 正是例外之一。

降级（§8.1 / §10 回退路径）：
    归因不准也只是分析结论不可用，不干预 Agent 运行。任何异常一律
    吞掉并返回空报告 —— 归因层的故障不得让任务失败（契约 1）。
"""
from __future__ import annotations

import logging
from typing import Any

from backend.necessity.hooks import TaskResult
from .classifier import AttributionResult, FailureClassifier, LLMClassifier
from .meta import (
    CoverageCheck,
    MetaEvalReport,
    check_signal_coverage,
    evaluate_attribution,
    failure_distribution,
    verify_improvement,
)

logger = logging.getLogger("necessity.attribution")

__all__ = ["AttributionHooks", "build_attribution_hooks"]


class AttributionHooks:
    """实现了 NecessityHooks 子集的部分实现（只关心生命周期）。

    它同时是一个**离线分析门面**：调用方拿到实例后可以直接跑
    `attribute_all()` / `report()`，不需要理解 signals / classifier 的细节。
    """

    def __init__(self, cfg: dict | None = None):
        cfg = dict(cfg or {})
        self.cfg = cfg
        self.trace = cfg.get("trace")          # 缺省 None → 延迟绑定单例
        self.llm: LLMClassifier | None = cfg.get("llm")
        self.task_meta: dict = cfg.get("task_meta") or {}
        self.session_id: str = cfg.get("session_id") or ""

        self._classifier = FailureClassifier(
            trace=self.trace, llm=self.llm, task_meta=self.task_meta,
        )
        self.results: list[AttributionResult] = []
        self._task: dict = {}

    # ── 生命周期（只有 on_task_end 有实体逻辑）────────────────────

    def on_task_start(self, task: dict) -> None:
        self._task = dict(task or {})
        if not self.session_id:
            self.session_id = str(
                self._task.get("session_id") or self._task.get("task_id") or ""
            )
        # 任务元数据可能带 task_issue 预标注（§4.2「人工预标注」）
        if self._task and not self.task_meta:
            self.task_meta = self._task
        if self.trace is None:
            self.trace = self._resolve_trace()
            self._classifier.trace = self.trace

    def on_task_end(self, result: TaskResult) -> dict:
        """任务结束：对失败尝试归因，返回报告。

        返回 dict 是契约 2 允许写存储的钩子之一；这里只返回，
        写库交给调用方（保持 core 与存储解耦）。
        """
        try:
            session = self.session_id or str(getattr(result, "task_id", ""))
            res = self.attribute(result, session_id=session)
            if res is None:
                return {"attributed": False, "reason": "任务通过，无需归因"}
            self.results.append(res)
            return {"attributed": True, "attribution": res.as_dict()}
        except Exception as e:  # 契约 1：归因故障不阻塞任务
            logger.warning("attribution on_task_end failed: %s", e)
            return {"attributed": False, "error": str(e)}

    # ── 离线分析入口 ─────────────────────────────────────────────

    def _resolve_trace(self) -> Any:
        try:
            from ..index.trace import get_trace
            return get_trace()
        except Exception:
            return None

    def attribute(self, result: Any, session_id: str = "",
                  task: dict | None = None) -> AttributionResult | None:
        """对单个尝试归因（pass 返回 None）。"""
        if self.trace is None:
            self.trace = self._resolve_trace()
            self._classifier.trace = self.trace
        return self._classifier.attribute(
            result, task=task or self.task_meta or None,
            session_id=session_id or self.session_id,
        )

    def result_for(self, attempt_id: str) -> AttributionResult | None:
        for r in self.results:
            if r.attempt_id == attempt_id:
                return r
        return None

    def distribution(self) -> dict[str, float]:
        """失败构成分布（只用 primary）。"""
        return failure_distribution(self.results)

    def coverage(self) -> CoverageCheck:
        """Gate 6：unknown 占比 / 缺失埋点。"""
        return check_signal_coverage(self.results, trace=self.trace)

    def meta_eval(self, human_labels: dict[str, str],
                  methods: dict[str, str] | None = None) -> MetaEvalReport:
        """元评测：与人 工标注比准确率 / 混淆矩阵（§6.2）。"""
        return evaluate_attribution(
            {r.attempt_id: r for r in self.results}, human_labels, methods=methods,
        )

    def verify(self, before: list[AttributionResult],
               after: list[AttributionResult], category: str, **kw) -> Any:
        """改进闭环验证（EVAL.md §5.1 第 ⑦ 步）。"""
        return verify_improvement(before, after, category, **kw)


def build_attribution_hooks(cfg: dict | None = None) -> AttributionHooks:
    """能力 4 的工厂入口 —— `core/capability.py::FACTORY_NAMES` 依赖此名。

    返回对象只实现 NecessityHooks 的子集，由 CompositeHooks 组合。
    """
    return AttributionHooks(cfg)
