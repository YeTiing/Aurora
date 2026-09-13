"""元评测的量化部分 —— 准确率 / 每类 P R / 混淆矩阵（ATTRIBUTION.md §6.2）。

与 meta.py 的分工：
    accuracy.py —— 「归因准不准」的统计计算
    meta.py     —— 标注循环性缓解、Gate 6 覆盖检查、改进闭环验证
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import taxonomy as tx

# §6.3：某两类混淆率 > 30% → 边界不清，应合并或拆分
CONFUSION_THRESHOLD = 0.30


@dataclass
class MetaEvalReport:
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    top_confusions: list[tuple[str, str, float]] = field(default_factory=list)
    by_method: dict[str, float] = field(default_factory=dict)
    mapping_failure_rate: float = 0.0

    def as_dict(self) -> dict:
        return {
            "total": self.total, "correct": self.correct, "accuracy": self.accuracy,
            "per_class": self.per_class, "confusion": self.confusion,
            "top_confusions": self.top_confusions, "by_method": self.by_method,
            "mapping_failure_rate": self.mapping_failure_rate,
        }


def evaluate_attribution(attributions: Mapping[str, Any],
                         human_labels: Mapping[str, str],
                         methods: Mapping[str, str] | None = None,
                         mapping: Any = None) -> MetaEvalReport:
    """对归因结果算准确率 / 每类 P R / 混淆矩阵 / 分层准确率。

    `attributions = {attempt_id: AttributionResult}`，`human_labels` 为 ground truth。
    只有两边都存在的 attempt 参与计算（缺人工标注的不算，避免虚高）。
    `by_method` 让「规则层 vs LLM 层」各自准确率可比（§9.4：LLM 层若低于规则层
    就是帮倒忙，应移除）。
    """
    rep = MetaEvalReport()
    if mapping is not None:
        rep.mapping_failure_rate = getattr(mapping, "mapping_failure_rate", 0.0)

    pairs = [(aid, attributions[aid], human_labels[aid])
             for aid in human_labels if aid in attributions]
    rep.total = len(pairs)
    if not pairs:
        return rep

    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    by_method_ok: Counter = Counter()
    by_method_all: Counter = Counter()

    for aid, attr, gold in pairs:
        pred = getattr(attr, "primary", attr if isinstance(attr, str) else tx.UNKNOWN)
        rep.confusion.setdefault(pred, {})
        rep.confusion[pred][gold] = rep.confusion[pred].get(gold, 0) + 1
        if pred == gold:
            rep.correct += 1
            tp[gold] += 1
        else:
            fp[pred] += 1
            fn[gold] += 1
        m = (methods or {}).get(aid, getattr(attr, "method", "")) or "unknown"
        by_method_all[m] += 1
        by_method_ok[m] += int(pred == gold)

    rep.accuracy = rep.correct / rep.total
    cats = sorted(set(tx.CATEGORIES) | {tx.UNKNOWN} | set(rep.confusion))
    for c in cats:
        if tp[c] or fp[c] or fn[c]:
            denom_p = tp[c] + fp[c]
            denom_r = tp[c] + fn[c]
            rep.per_class[c] = {
                "precision": tp[c] / denom_p if denom_p else 0.0,
                "recall": tp[c] / denom_r if denom_r else 0.0,
                "support": denom_r,
            }

    # §6.3：哪些类容易混 —— 只统计「确实错了」的方向对（gold → pred）
    conf_pairs: Counter = Counter()
    for pred, golds in rep.confusion.items():
        for gold, n in golds.items():
            if pred != gold:
                conf_pairs[(gold, pred)] += n
    rep.top_confusions = [
        (g, p, n / rep.total) for (g, p), n in conf_pairs.most_common()
    ]
    rep.by_method = {m: by_method_ok[m] / by_method_all[m] for m in by_method_all}
    return rep


def needs_taxonomy_revision(rep: MetaEvalReport,
                            threshold: float = CONFUSION_THRESHOLD
                            ) -> list[tuple[str, str, float]]:
    """混淆率 > 30% 的类对 → 分类体系边界不清，应合并或拆分（§6.3）。

    **分类体系是迭代出来的，不是一次设计出来的。**
    """
    return [pair for pair in rep.top_confusions if pair[2] > threshold]


__all__ = ["CONFUSION_THRESHOLD", "MetaEvalReport", "evaluate_attribution",
           "needs_taxonomy_revision"]
