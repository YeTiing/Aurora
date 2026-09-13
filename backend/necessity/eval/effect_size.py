"""效应量与判定 —— 从 stats.py 拆出（超 300 行上限）。

为什么效应量必须和 p 值分开看（EVAL.md §6.3）：
    n=30 的统计功效低，p 值检测不出小效应。所以判据用 **Cliff's delta**
    而非 p 值，且结论严格限定在本任务集上，不外推。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence


# ── 效应量 ───────────────────────────────────────────────────────

# 判定阈值（EVAL.md §6.3）。定义在本模块而非 stats.py ——
# make_verdict 要用它们，放在 stats.py 会造成循环导入。
DELTA_LARGE = 0.47        # |delta| > 0.47 视为大效应量
P_SIGNIFICANT = 0.05      # 惯例阈值


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """Cliff's delta（dominance）：P(a>b) - P(a<b)，取值 [-1, 1]。

    为什么不用 Cohen's d：d 假设近似正态且对离群值敏感；§6.1 明说任务
    难度分布不均。Cliff's delta 是非参数效应量，与配对设计搭配时
    在组间比较上比 d 稳健。
    """
    if not a or not b:
        return 0.0
    gt = lt = 0
    for x in a:
        for y in b:
            if x > y:
                gt += 1
            elif x < y:
                lt += 1
    return (gt - lt) / (len(a) * len(b))


def delta_magnitude(delta: float) -> str:
    """Cliff's delta 的惯例分档（|delta|）。"""
    d = abs(delta)
    if d < 0.147:
        return "negligible"
    if d < 0.33:
        return "small"
    if d < 0.474:
        return "medium"
    return "large"


# ── 描述统计与 Bootstrap ─────────────────────────────────────────

def median(values: Sequence[float]) -> float:
    """中位数（§2.3：任务难度不均，中位数才反映典型情况）。

    偶数个取中间两数平均 —— statistics.median 同款，但这里显式实现
    以避免调用方对实现差异产生意外。
    """
    vals = sorted(float(v) for v in values)
    n = len(vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def iqr(values: Sequence[float]) -> tuple[float, float]:
    """四分位距（Q1, Q3），用线性插值法。"""
    vals = sorted(float(v) for v in values)
    n = len(vals)
    if n == 0:
        return 0.0, 0.0

    def q(p: float) -> float:
        if n == 1:
            return vals[0]
        pos = p * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return vals[lo] * (1 - frac) + vals[hi] * frac

    return q(0.25), q(0.75)


def median_diff(a: Sequence[float], b: Sequence[float]) -> float:
    """配对中位数差 —— **不是** 中位数之差。

    §6.2 要求配对比：先逐对求差，再取中位数。median(a) - median(b) 是
    两个独立统计量之差，会掩盖配对结构（这正是大多数项目"作弊"的地方）。
    """
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return median([a[i] - b[i] for i in range(n)])


def bootstrap_ci(
    values: Sequence[float],
    *,
    n_boot: int = 1000,
    seed: int = 20260913,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Bootstrap 均值置信区间（§6.2 要求 1000 次重采样）。

    固定 seed：报告里的区间必须可复现，否则审阅者无法核对。
    """
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return vals[0], vals[0]
    rng = random.Random(seed)
    means = sorted(
        sum(rng.choices(vals, k=n)) / n for _ in range(max(1, n_boot))
    )
    lo = means[int((alpha / 2) * len(means))]
    hi = means[min(len(means) - 1, int((1 - alpha / 2) * len(means)))]
    return lo, hi


# ── §6.3 报告口径 ────────────────────────────────────────────────

@dataclass
class Verdict:
    """一次对比的结论 —— 把 §6.3 的口径直接编码成数据，避免报告手写措辞。"""
    delta: float
    p_value: float | None
    n_pairs: int
    claim: str                       # "significant" | "trend" | "none" | "undefined"
    text: str
    scoped_to_task_set: bool = True  # 恒为 True：结论只限本任务集
    detail: dict = field(default_factory=dict)


def make_verdict(delta: float, p_value: float | None, n_pairs: int,
                 label: str = "", metric: str = "") -> Verdict:
    """按 §6.3 生成结论文字。

    §6.3 原文：
      ✅ 若效应量大（Cliff's delta > 0.47）且 p < 0.05
         → 可称「在该任务集上观察到显著差异」
      ⚠️ 若效应量小但方向一致
         → 只能说「观察到趋势，样本量不足以确认」
      ❌ 不宣称 p < 0.05 就等于「普遍有效」—— 结论限于本任务集，不外推

    实现上的两个诚实点：
      1. p_value 为 None（无法计算）时**绝不算显著**，即使 delta 很大
      2. 方向由 delta 的符号决定；n_pairs==0 时连「趋势」都不能说
    """
    # 拼成「E 的『重复读取率』」，避免报告里出现读起来像机器翻译的短语
    if label and metric:
        name = f"{label} 的「{metric}」"
    elif label or metric:
        name = f"{label or metric} 的对比"
    else:
        name = "该对比"
    detail = {"delta": round(delta, 4), "p_value": p_value, "n_pairs": n_pairs}

    if n_pairs == 0 or p_value is None:
        return Verdict(delta, p_value, n_pairs, "undefined",
                       "样本不足或无法计算检验统计量，不作任何结论。", True, detail)

    if abs(delta) > DELTA_LARGE and p_value < P_SIGNIFICANT:
        direction = "改善" if delta > 0 else "劣化"
        # 注意「在该任务集上」——这是 §6.3 规定的限定语，不可省略
        text = (f"在该任务集上观察到 {name} 的显著{direction}"
                f"（Cliff's delta={delta:.3f}，p={p_value:.4f}，n={n_pairs}）。"
                "结论仅限本任务集，不外推。")
        return Verdict(delta, p_value, n_pairs, "significant", text, True, detail)

    if abs(delta) > 0.147:      # 至少小效应量才谈得上方向
        direction = "优于" if delta > 0 else "劣于"
        p_txt = "p 无法计算" if p_value is None else f"p={p_value:.4f}"
        text = (f"观察到 {name} 的趋势（{direction}对照，"
                f"Cliff's delta={delta:.3f}，{p_txt}，n={n_pairs}），"
                "样本量不足以确认。结论仅限本任务集，不外推。")
        return Verdict(delta, p_value, n_pairs, "trend", text, True, detail)

    return Verdict(delta, p_value, n_pairs, "none",
                   f"{name} 未见有意义差异（Cliff's delta={delta:.3f}，n={n_pairs}）。",
                   True, detail)
