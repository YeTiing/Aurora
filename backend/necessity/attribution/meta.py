"""元评测 —— 归因本身的验证（ATTRIBUTION.md §6）+ 改进闭环（EVAL.md §5）。

为什么必须验证（§6.1）：
    归因可能不准。**不报告准确率，失败构成分布就不可信**，
    基于它的改进决策也会错。「不报告准确率的归因等于猜。」

⚠️ 本模块不解决的循环性（诚实声明）：
    §6.2 要求人工按**同一套分类**标注，再算一致率。但分类体系是自定的，
    因此高一致率只证明「标注者与分类器自洽」，**不证明分类体系正确**。
    缓解见 `map_free_text_label`：让标注者先写自由文本，再映射到分类，
    并报告**映射失败率**。失败率高 = 分类体系缺类，而不是标注者错了。
    这仍不能完全消除循环性（映射由人做），但把「体系是否够用」变成了
    一个可观测的数字 —— 这是本模块能做到的诚实上限；**局限本身必须在
    报告里写明，不能假装解决了**。

分工：量化统计（准确率 / 混淆矩阵）在 `accuracy.py`。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from . import taxonomy as tx
from .accuracy import MetaEvalReport, evaluate_attribution, needs_taxonomy_revision  # noqa: F401

# §9.5 / Gate 6：unknown 占比 > 40% → 信号覆盖不足，补埋点
UNKNOWN_GATE = 0.40
# 自由文本映射失败率高于该值 → 分类体系缺类
MAPPING_FAILURE_THRESHOLD = 0.20


# ── 自由文本标注 → 分类（循环性缓解）──────────────────────────────

# 自由文本里的关键词 → 分类。命中最多者胜，全不命中即映射失败。
_FREE_TEXT_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (tx.ENVIRONMENT, ("环境", "依赖", "网络", "权限", "超时", "flaky", "装不",
                      "install", "import error", "connection", "permission", "env")),
    (tx.TASK_ISSUE, ("任务本身", "需求矛盾", "不可完成", "无法完成", "验收测试本身",
                     "baseline", "impossible", "contradict")),
    (tx.CONSTRAINT_DRIFT, ("越界", "约束", "改了不该改", "超出范围", "out of scope",
                           "constraint", "violat")),
    (tx.THRASHING, ("反复", "来回", "回滚", "打转", "不收敛", "thrash", "revert",
                    "churn")),
    (tx.TOOL_USE, ("工具", "参数", "调用失败", "选错工具", "tool", "argument")),
    (tx.CONTEXT_STALE, ("过期", "失效", "旧版本", "stale", "outdated", "过时")),
    (tx.CONTEXT_MISSING, ("没读", "没看", "遗漏", "上下文丢失", "压缩", "检索不到",
                          "missing context", "never read", "compress")),
    (tx.EDIT_ERROR, ("写错", "语法", "缩进", "改错位置", "拼写", "syntax",
                     "indent", "typo")),
    (tx.PLANNING, ("规划", "分解", "顺序", "方向", "计划", "plan", "ordering",
                   "approach")),
    (tx.CAPABILITY, ("能力", "模型不行", "推理", "知识", "不会", "capability",
                     "reasoning", "knowledge")),
)


@dataclass
class LabelMappingResult:
    """自由文本标注的映射结果 + 映射失败率。"""
    mapped: list[tuple[str, str]] = field(default_factory=list)   # (attempt_id, category)
    unmapped: list[str] = field(default_factory=list)             # attempt_id

    @property
    def mapping_failure_rate(self) -> float:
        total = len(self.mapped) + len(self.unmapped)
        return len(self.unmapped) / total if total else 0.0

    def taxonomy_inadequate(self, threshold: float = MAPPING_FAILURE_THRESHOLD) -> bool:
        """映射失败率高 → 分类体系缺类，应先扩分类再谈准确率。"""
        return self.mapping_failure_rate > threshold

    def as_dict(self) -> dict:
        return {
            "mapped": self.mapped, "unmapped": self.unmapped,
            "mapping_failure_rate": self.mapping_failure_rate,
            "taxonomy_inadequate": self.taxonomy_inadequate(),
        }


def map_free_text_label(text: str) -> str | None:
    """把自由文本标注映射到分类；映射不出返回 None。

    返回 None 是**有信息的** —— 它意味着分类体系里没有能表达该原因的类。
    """
    if not text:
        return None
    low = text.lower()
    scored: list[tuple[int, int, str]] = []
    for idx, (cat, keys) in enumerate(_FREE_TEXT_KEYS):
        hits = sum(1 for k in keys
                   if (k in low if k.isascii() else k in text))
        if hits:
            scored.append((hits, -idx, cat))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


def map_free_text_labels(labels: Mapping[str, str]) -> LabelMappingResult:
    """批量映射自由文本标注。`labels = {attempt_id: free_text}`。"""
    res = LabelMappingResult()
    for aid, text in labels.items():
        cat = map_free_text_label(text)
        if cat is None:
            res.unmapped.append(aid)
        else:
            res.mapped.append((aid, cat))
    return res


# ── Gate 6：信号覆盖检查（EVAL.md §4 / ATTRIBUTION.md §9.5）───────

@dataclass
class CoverageCheck:
    """`unknown` 占比 > 40% → 信号覆盖不足，补埋点。"""
    unknown_ratio: float
    total: int
    unknown: int
    missing_event_kinds: list[str] = field(default_factory=list)
    insufficient: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "unknown_ratio": self.unknown_ratio, "total": self.total,
            "unknown": self.unknown, "missing_event_kinds": self.missing_event_kinds,
            "insufficient": self.insufficient, "note": self.note,
        }


def check_signal_coverage(attributions: Iterable[Any], trace: Any = None,
                          threshold: float = UNKNOWN_GATE) -> CoverageCheck:
    """unknown 占比 > 40% 或有必需事件从未被采集 → 信号覆盖不足。

    `TraceStore.missing_event_kinds()` 是这个判断的直接输入。
    """
    items = list(attributions)
    total = len(items)
    unknown = sum(1 for a in items if getattr(a, "primary", "") == tx.UNKNOWN)
    ratio = unknown / total if total else 0.0
    missing: list[str] = []
    if trace is not None and hasattr(trace, "missing_event_kinds"):
        try:
            missing = list(trace.missing_event_kinds())
        except Exception:
            missing = []
    insufficient = ratio > threshold or bool(missing)
    notes: list[str] = []
    if ratio > threshold:
        notes.append(f"unknown 占比 {ratio:.0%} > {threshold:.0%}，强归因会污染统计")
    if missing:
        notes.append(f"以下必需事件从未被采集：{', '.join(missing)}")
    if not notes:
        notes.append(f"unknown 占比 {ratio:.0%}，信号覆盖充分")
    return CoverageCheck(
        unknown_ratio=ratio, total=total, unknown=unknown,
        missing_event_kinds=missing, insufficient=insufficient,
        note="；".join(notes),
    )


# ── 改进闭环验证（EVAL.md §5.1 第 ⑦ 步）──────────────────────────

@dataclass
class LoopVerification:
    """该类占比是否下降。

    **诚实报告**：没下降就是没下降，不能粉饰 —— 那说明归因错误或
    改进方向错，应回到第 ③ 步复核（EVAL.md §5.2）。
    """
    category: str
    before_ratio: float
    after_ratio: float
    before_total: int
    after_total: int
    target_dropped: bool
    completion_improved: bool | None = None
    note: str = ""
    caveat: str = ""

    def as_dict(self) -> dict:
        return {
            "category": self.category, "before_ratio": self.before_ratio,
            "after_ratio": self.after_ratio, "before_total": self.before_total,
            "after_total": self.after_total, "target_dropped": self.target_dropped,
            "completion_improved": self.completion_improved,
            "note": self.note, "caveat": self.caveat,
        }


def _proportion(attributions: Iterable[Any], category: str) -> tuple[float, int]:
    items = [a for a in attributions if getattr(a, "primary", None)]
    total = len(items)
    n = sum(1 for a in items if getattr(a, "primary", "") == category)
    return (n / total if total else 0.0), total


def verify_improvement(before: Iterable[Any], after: Iterable[Any], category: str,
                       completion_before: float | None = None,
                       completion_after: float | None = None) -> LoopVerification:
    """给定改进前后的归因结果，判断目标类占比是否下降（统计只用 primary）。

    闭环成立 = 至少完成一轮「归因 → 改进 → 重测 → 该分类占比下降」。
    **这本身就是一个可报告的结果**，即使最终数字不惊艳（EVAL.md §5.3）。
    """
    b_ratio, b_total = _proportion(before, category)
    a_ratio, a_total = _proportion(after, category)
    dropped = a_ratio < b_ratio
    comp = None
    if completion_before is not None and completion_after is not None:
        comp = completion_after > completion_before
    if dropped:
        note = (f"闭环成立：{category} 占比从 {b_ratio:.0%} 降到 {a_ratio:.0%}"
                f"（{b_total} → {a_total} 个失败尝试）")
    else:
        note = (f"闭环未成立：{category} 占比未下降（{b_ratio:.0%} → {a_ratio:.0%}）。"
                f"说明归因错误或改进方向错，应回到归因步骤复核")
    caveat = ""
    if comp is False:
        caveat = "总完成率下降 —— 该改进可能伤害了整体，EVAL.md §5.2 要求撤销"
    return LoopVerification(
        category=category, before_ratio=b_ratio, after_ratio=a_ratio,
        before_total=b_total, after_total=a_total, target_dropped=dropped,
        completion_improved=comp, note=note, caveat=caveat,
    )


def failure_distribution(attributions: Iterable[Any]) -> dict[str, float]:
    """失败构成分布（统计只用 primary，§2.4）—— 指导改进优先级。

    多因失败会同时出现在 primary 与 contributing 里，若两者都统计会
    重复计数，夸大某些类的占比。
    """
    items = [getattr(a, "primary", "") for a in attributions]
    items = [c for c in items if c]
    if not items:
        return {}
    counts = Counter(items)
    n = len(items)
    return {c: counts[c] / n for c, _ in counts.most_common()}


__all__ = [
    "MAPPING_FAILURE_THRESHOLD", "UNKNOWN_GATE", "CoverageCheck",
    "LabelMappingResult", "LoopVerification", "MetaEvalReport",
    "check_signal_coverage", "evaluate_attribution", "failure_distribution",
    "map_free_text_label", "map_free_text_labels", "needs_taxonomy_revision",
    "verify_improvement",
]
