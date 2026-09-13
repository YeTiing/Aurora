"""能力 4：Failure Attribution —— 失败根因归因。

把「任务失败了」变成「**因为什么失败、下一步该改哪里**」。

模块（ATTRIBUTION.md §7 / INTEGRATION.md §2）：
    taxonomy.py   分类体系（设计核心，§2）
    signals.py    确定性信号提取（Layer 1，§4）
    classifier.py 规则 + LLM 辅助（Layer 1/2，§3 / §5）
    meta.py       元评测 + 改进闭环（§6 / EVAL.md §5）
    hooks.py      钩子装配与离线分析门面

入口（硬契约）：`build_attribution_hooks(cfg)`。
"""
from __future__ import annotations

from .classifier import (
    AttributionResult,
    FailureClassifier,
    LLMClassifier,
    build_trace_summary,
)
from .hooks import AttributionHooks, build_attribution_hooks
from .meta import (
    CoverageCheck,
    LabelMappingResult,
    LoopVerification,
    MetaEvalReport,
    check_signal_coverage,
    evaluate_attribution,
    failure_distribution,
    map_free_text_label,
    map_free_text_labels,
    needs_taxonomy_revision,
    verify_improvement,
)
from .signals import Signal, extract_signals, pick_primary
from .taxonomy import (
    CATEGORIES,
    IMPROVEMENTS,
    NON_AGENT_FAULT,
    UNKNOWN,
    improvement_for,
    is_agent_fault,
    is_known,
)

__all__ = [
    # 入口
    "AttributionHooks", "build_attribution_hooks",
    # 分类体系
    "CATEGORIES", "IMPROVEMENTS", "NON_AGENT_FAULT", "UNKNOWN",
    "improvement_for", "is_agent_fault", "is_known",
    # 信号 / 分类
    "AttributionResult", "FailureClassifier", "LLMClassifier", "Signal",
    "build_trace_summary", "extract_signals", "pick_primary",
    # 元评测 / 闭环
    "CoverageCheck", "LabelMappingResult", "LoopVerification", "MetaEvalReport",
    "check_signal_coverage", "evaluate_attribution", "failure_distribution",
    "map_free_text_label", "map_free_text_labels", "needs_taxonomy_revision",
    "verify_improvement",
]
