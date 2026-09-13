"""统计核心 —— EVAL.md §6 的诚实实现（纯 stdlib，不引 scipy）。

为什么自己写而不是用 scipy：
    §6.1 的诚实前提是「样本量小（n=30）、功效低」。scipy 的存在会诱使人
    去用 t 检验（假设正态）或把 p 值当成结论。Wilcoxon 与 Cliff's delta
    都只有十几行，手写反而逼我们把**口径**写死在代码里：

      - 配对设计（§6.2）：只比较配对差，绝不拿两组独立均值对比
      - n<6 时 Wilcoxon 的精确分布覆盖不到 5%，必须显式拒绝声称显著
        （正态近似在 n=5 与精确值差近一倍 —— 这是最容易静默出错的地方）
      - 零差丢弃（Wilcoxon 原版做法），全零 → 返回 None 而非 0，
        因为「无法计算」与「无差异」是两件事

参考：Wilcoxon (1945) 原版符号秩检验；Cliff (1993) dominance statistic。
"""
from __future__ import annotations

from typing import NamedTuple, Sequence

# §6.3 的判据阈值
# 阈值定义在 effect_size.py（make_verdict 要用），这里转发保持既有引用可用
from .effect_size import DELTA_LARGE, P_SIGNIFICANT  # noqa: E402,F401


class WilcoxonResult(NamedTuple):
    """Wilcoxon 符号秩检验结果。

    p_value 为 None 表示**无法计算**（样本全零差，或有效对数 n<1）。
    这与 p=1.0（无差异证据）语义不同，调用方必须区别对待。
    """
    statistic: float
    p_value: float | None
    n: int                # 非零配对差的数量（零差已按原版做法丢弃）
    method: str           # exact / approx / undefined


# ── 组合数（精确分布的权重；用整数避免浮点误差）────────────────────

def _binom(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    k = min(k, n - k)
    num = 1
    for i in range(k):
        num = num * (n - i) // (i + 1)
    return num


# ── 秩（处理并列：取平均秩）──────────────────────────────────────

def _ranks(values: Sequence[float]) -> list[float]:
    """平均秩（1-based）。并列取平均是 Wilcoxon 的标准做法，否则
    p 值会因并列被人为压低。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0      # 名次区间 [i, j] → 平均秩
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _exact_distribution(n: int) -> dict[float, float]:
    """精确零分布：W+ 的每个取值对应的组合数（整数权）。

    对每个子集 S，W+ = Σ_{i∈S} i。递推 dp[sum] += dp[sum-i]，
    等价于生成函数 ∏(1 + x^i)，总和 2^n。n=25 时约 3.4e7 的整数求和，
    在纯 Python 里仍是亚秒级；再大就走正态近似。
    """
    dp: dict[int, int] = {0: 1}
    for i in range(1, n + 1):
        nxt = dict(dp)
        for s, c in dp.items():
            nxt[s + i] = nxt.get(s + i, 0) + c
        dp = nxt
    return {float(k): float(v) for k, v in dp.items()}


# ── 主检验 ───────────────────────────────────────────────────────

def wilcoxon_signed_rank(
    x: Sequence[float],
    y: Sequence[float] | None = None,
    *,
    exact_max_n: int = 25,
) -> WilcoxonResult:
    """配对 Wilcoxon 符号秩检验（双尾）。

    用法两种，语义相同：
        wilcoxon_signed_rank(differences)      # 直接给配对差
        wilcoxon_signed_rank(a, b)             # 给两组，内部做 a-b

    为什么零差必须丢弃：Wilcoxon 原版如此，且零差没有方向信息。
    丢弃后 n 变小 → 功效更低 → 更保守，这是可接受的方向。
    """
    diffs = [float(a - b) for a, b in zip(x, y)] if y is not None else [float(v) for v in x]
    nz = [d for d in diffs if d != 0.0]
    n = len(nz)
    if n == 0:
        # 全部相等：没有可检验的信息。返回 None 而非 p=1.0，
        # 否则报告会写成「无显著差异」，把「无法判断」说成了结论
        return WilcoxonResult(0.0, None, 0, "undefined")

    ranks = _ranks([abs(d) for d in nz])
    w_plus = sum(r for r, d in zip(ranks, nz) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, nz) if d < 0)
    w = min(w_plus, w_minus)

    if n <= exact_max_n:
        dist = _exact_distribution(n)
        total = float(2 ** n)
        # 双尾：P(W+ ≤ w) + P(W+ ≥ n(n+1)/2 - w)，对称分布下即 2*P(W+ ≤ w)
        p = 2.0 * sum(c for s, c in dist.items() if s <= w) / total
        return WilcoxonResult(w, min(1.0, p), n, "exact")

    # n>25：正态近似（带连续性校正；有并列时方差需减修正项）
    import math
    mean = n * (n + 1) / 4.0
    var = n * (n + 1) * (2 * n + 1) / 24.0
    ties = _tie_correction(nz, ranks)
    var -= ties
    if var <= 0:
        return WilcoxonResult(w, None, n, "undefined")
    z = (w - mean + 0.5) / math.sqrt(var)
    p = 2.0 * 0.5 * math.erfc(abs(z) / math.sqrt(2.0))
    return WilcoxonResult(w, min(1.0, p), n, "approx")


def _tie_correction(nz: Sequence[float], ranks: Sequence[float]) -> float:
    """并列修正 Σ(t³-t)/48（t = 各并列组的个数）。"""
    from collections import Counter
    counts = Counter(round(r, 6) for r in ranks)
    return sum(t ** 3 - t for t in counts.values() if t > 1) / 48.0


# 效应量与判定已拆到 effect_size.py（stats.py 只放检验）。
# DELTA_LARGE / P_SIGNIFICANT 仍定义在 stats.py（检验与判定共用的阈值），
# 所以这里只转发 effect_size 里的函数与 Verdict。
from .effect_size import (  # noqa: E402,F401
    Verdict, bootstrap_ci, cliffs_delta, delta_magnitude, iqr,
    make_verdict, median, median_diff,
)
