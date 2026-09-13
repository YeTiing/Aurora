"""eval/stats.py 与 eval/report.py 的测试 —— 统计必须逐位对得上手算值。

为什么这一份测试格外较真：
    §6.3 的结论一旦写错，就是把「无法判断」说成了「显著有效」。
    p 值和 delta 都是可手算的小样本，没有理由不逐个核对。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.necessity.eval.records import Attempt, GateMetrics, Status  # noqa: E402
from backend.necessity.eval.report import (  # noqa: E402
    DISCLAIMER,
    all_comparisons,
    build_report,
    group_median,
    render_table,
    summaries,
)
from backend.necessity.eval.stats import (  # noqa: E402
    cliffs_delta,
    delta_magnitude,
    iqr,
    make_verdict,
    median,
    median_diff,
    wilcoxon_signed_rank,
)


# ── Wilcoxon：手算值 ─────────────────────────────────────────────

def test_wilcoxon_all_positive_n6_exact():
    """6 个差全为正、无并列 → W=0，精确双尾 p = 2×(1/64) = 0.03125。

    推导：W+ 的零分布是 ∏(1+x^i) 的系数，W+=0 只有空集一种，概率 1/64。
    双尾 = 2 × P(W+ ≤ 0) = 2/64。
    """
    r = wilcoxon_signed_rank([1, 2, 3, 4, 5, 6])
    assert r.statistic == 0
    assert r.n == 6
    assert r.method == "exact"
    assert math.isclose(r.p_value, 0.03125, rel_tol=0, abs_tol=1e-12)


def test_wilcoxon_n5_all_positive():
    """n=5 全正 → p = 2/32 = 0.0625（经典的「n=5 检不出 5% 显著」）。"""
    r = wilcoxon_signed_rank([3, 1, 4, 1, 5])
    assert r.p_value is not None and math.isclose(r.p_value, 0.0625, abs_tol=1e-12)


def test_wilcoxon_mixed_hand_computed():
    """差 [1, 2, -3] → |.| 秩 1,2,3；W+=3, W-=3, W=3。

    P(W+ ≤ 3) = 系数和 (x^0+x^1+x^2+x^3) / 8 = 5/8（空集/{1}/{2}/{3}/{1,2}），
    双尾 min(1, 2×5/8) = 1.0。
    """
    r = wilcoxon_signed_rank([1, 2, -3])
    assert r.statistic == 3
    assert r.n == 3
    assert r.p_value is not None and math.isclose(r.p_value, 1.0, abs_tol=1e-12)


def test_wilcoxon_ties_use_average_rank():
    """并列取平均秩：|differences| = [1,1,2] → 秩 1.5,1.5,3，W+=6。"""
    r = wilcoxon_signed_rank([1, 1, 2])
    assert math.isclose(r.statistic, 0.0)          # 对称 → W 取小者为 0
    assert r.n == 3


def test_wilcoxon_zero_differences_discarded():
    """零差按 Wilcoxon 原版做法丢弃，且不参与 n。"""
    r = wilcoxon_signed_rank([1, 0, 0, 2, -1])
    assert r.n == 3                                # 只有 1,2,-1 三个非零


def test_wilcoxon_all_zero_is_undefined_no_crash():
    """退化情形：全零差 → 不得崩，也不得谎报 p=1.0。

    返回 None 的理由：p=1.0 会被报告读成「无差异」，而事实是「无法判断」。
    """
    r = wilcoxon_signed_rank([0, 0, 0, 0])
    assert r.p_value is None
    assert r.n == 0
    assert r.method == "undefined"


def test_wilcoxon_two_sample_form_matches_diff_form():
    a = [10, 12, 14, 16]
    b = [8, 9, 10, 11]
    assert wilcoxon_signed_rank(a, b) == wilcoxon_signed_rank(
        [a[i] - b[i] for i in range(len(a))])


def test_wilcoxon_large_n_uses_approx():
    r = wilcoxon_signed_rank(list(range(1, 31)))   # n=30 > exact_max_n
    assert r.method == "approx"
    assert r.p_value is not None and r.p_value < 0.05


# ── Cliff's delta：边界与已知中值 ────────────────────────────────

def test_cliffs_delta_boundaries():
    assert cliffs_delta([4, 5, 6], [1, 2, 3]) == 1.0       # 完全支配
    assert cliffs_delta([1, 2, 3], [4, 5, 6]) == -1.0      # 完全被支配
    assert cliffs_delta([1, 2, 3], [1, 2, 3]) == 0.0       # 完全相同


def test_cliffs_delta_known_mid_value():
    """a=[1,2], b=[2,3]：9 对中 gt=1 (2>... 实际 2>2 false)...

    逐对：1>2 F,1>3 F,1<2 T,1<3 T, 2>2 F,2>3 F,2<2 F,2<3 T…
    正确列表：a=[1,2] vs b=[2,3]
      1 vs 2 → lt; 1 vs 3 → lt; 2 vs 2 → tie; 2 vs 3 → lt
    gt=0, lt=3 → delta = -3/4 = -0.75
    """
    assert cliffs_delta([1, 2], [2, 3]) == -0.75
    # 镜像得到 +0.75
    assert cliffs_delta([2, 3], [1, 2]) == 0.75


def test_cliffs_delta_empty_is_zero():
    assert cliffs_delta([], [1, 2]) == 0.0
    assert cliffs_delta([1], []) == 0.0


def test_delta_magnitude_thresholds():
    assert delta_magnitude(0.1) == "negligible"
    assert delta_magnitude(0.2) == "small"
    assert delta_magnitude(0.4) == "medium"
    assert delta_magnitude(0.5) == "large"
    assert delta_magnitude(-0.9) == "large"       # 取绝对值


# ── 描述统计 ─────────────────────────────────────────────────────

def test_median_odd_even():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 2, 3]) == 2.5
    assert median([]) == 0.0


def test_iqr_known():
    assert iqr([1, 2, 3, 4, 5]) == (2.0, 4.0)
    assert iqr([1, 2, 3, 4]) == (1.75, 3.25)


def test_median_diff_is_paired_not_difference_of_medians():
    """配对中位数差 ≠ 中位数之差 —— 这组数据刻意让两者不同。

    a=[10, 1, 1], b=[1, 1, 10]: 配对差 = [9, 0, -9] → 中位数 0。
    median(a)=1, median(b)=1 → 中位数之差 0（碰巧相同，用另一组区分）：
    a=[10, 20, 30], b=[1, 1, 1] → 配对差中位数 19；median 差 29-1=19 相同。
    真正能区分的：a=[5,5,100], b=[1,50,50]:
      配对差 = [4,-45,50] → 中位数 4
      median(a)=5, median(b)=50 → 差 -45
    """
    assert median_diff([5, 5, 100], [1, 50, 50]) == 4
    assert median([5, 5, 100]) - median([1, 50, 50]) == -45


# ── §6.3 判定口径 ────────────────────────────────────────────────

def test_verdict_significant_requires_both_delta_and_p():
    v = make_verdict(0.8, 0.01, 30, label="E", metric="重复读取率")
    assert v.claim == "significant"
    assert "在该任务集上" in v.text          # §6.3 的限定语
    assert "不外推" in v.text


def test_verdict_large_delta_but_p_missing_is_not_significant():
    """p 无法计算（全零差）时，delta 再大也不得称显著。"""
    v = make_verdict(0.9, None, 30)
    assert v.claim == "undefined"
    assert "不作任何结论" in v.text


def test_verdict_small_effect_says_trend_insufficient():
    v = make_verdict(0.2, 0.03, 30, label="E", metric="token")
    assert v.claim == "trend"
    assert "样本量不足" in v.text


def test_verdict_delta_at_threshold_is_not_significant():
    """§6.3 是 delta > 0.47（严格大于），恰好 0.47 不算大效应量。"""
    v = make_verdict(0.47, 0.01, 30)
    assert v.claim == "trend"


def test_verdict_wording_avoids_machine_translation():
    """措辞可读性也是「诚实报告」的一部分 —— 读起来像机翻没人会信。"""
    v = make_verdict(0.8, 0.01, 30, label="E", metric="重复读取率")
    assert "E 的「重复读取率」" in v.text


def test_verdict_n5_cannot_claim_significant():
    """n=5 全同向时精确 p=0.0625 > 0.05 —— 再大的 delta 也不能称显著。

    这正是 §6.1「统计功效低」的量化体现：小样本下 p 值天然检不出。
    """
    r = wilcoxon_signed_rank([1, 2, 3, 4, 5])
    v = make_verdict(1.0, r.p_value, r.n, label="E", metric="token")
    assert v.claim == "trend"
    assert "样本量不足" in v.text


def test_verdict_never_claims_universality():
    """任何判定都不得出现「普遍有效」式的全称断言。"""
    for d, p in ((0.9, 0.001), (0.3, 0.04), (0.05, 0.9), (0.0, None), (-0.9, 0.001)):
        v = make_verdict(d, p, 30)
        assert "普遍" not in v.text
        assert "所有任务" not in v.text
        assert "证明了" not in v.text


def test_verdict_direction_follows_delta_sign():
    assert "改善" in make_verdict(0.8, 0.01, 30).text
    assert "劣化" in make_verdict(-0.8, 0.01, 30).text


# ── 报告聚合 ─────────────────────────────────────────────────────

def _att(task_id: str, arm: str, run: int, *, status: Status = "pass",
         tokens: int = 1000, waste: float = 0.0, rho: float = 1.0) -> Attempt:
    g = GateMetrics(tokens_total=tokens, reads_total=100,
                    reads_waste=int(waste * 100), constraint_rho=rho)
    return Attempt(attempt_id=f"{task_id}#{arm}#{run}", task_id=task_id, arm=arm,
                   run_index=run, status=status, tokens=tokens, gates=g)


def test_group_median_takes_median_of_runs():
    runs = [_att("t1", "A", 0, tokens=100), _att("t1", "A", 1, tokens=900),
            _att("t1", "A", 2, tokens=200)]
    assert group_median(runs, lambda a: float(a.tokens)) == 200


def test_summaries_aggregate_by_arm():
    attempts = [_att(f"t{i}", "A", r, tokens=100) for i in range(3) for r in range(3)]
    s = summaries(attempts)["A"]
    assert s.n_tasks == 3
    assert s.n_attempts == 9
    assert s.completion_rate == 1.0
    assert s.medians["token"] == 100


def test_comparison_uses_paired_medians_not_replicates():
    """3 任务 × 3 次重复：配对数必须是 3（任务数），不是 9（运行数）。"""
    attempts = []
    for i in range(3):
        for r in range(3):
            attempts.append(_att(f"t{i}", "A", r, tokens=1000))
            attempts.append(_att(f"t{i}", "B", r, tokens=500))
    comps = [c for c in all_comparisons(attempts, baseline="A")
             if c.metric == "token" and c.arm == "B" and c.baseline == "A"]
    assert len(comps) == 1
    assert comps[0].n_pairs == 3
    assert comps[0].delta == 1.0          # B 的 token 更低 → 改善（方向已翻转）
    assert comps[0].median_diff == 500.0  # 配对差 1000-500，方向调整后仍 +500


def test_comparison_lower_is_better_direction_flipped():
    """重复读取率降低应得正效应量（改善），而不是负的。"""
    attempts = []
    for i in range(4):
        attempts.append(_att(f"t{i}", "A", 0, waste=0.30))
        attempts.append(_att(f"t{i}", "E", 0, waste=0.05))
    comps = [c for c in all_comparisons(attempts) if c.arm == "E"
             and c.metric == "重复读取率"]
    assert comps and comps[0].delta > 0
    assert comps[0].median_diff > 0


def test_e_is_compared_against_a_prime():
    """§3.1：E 必须额外对比 A′（最关键对照）。"""
    attempts = []
    for i in range(4):
        attempts.append(_att(f"t{i}", "E", 0, tokens=100))
        attempts.append(_att(f"t{i}", "A_prime", 0, tokens=900))
    comps = all_comparisons(attempts, baseline="A", vs_extra="A_prime")
    assert any(c.arm == "E" and c.baseline == "A_prime" for c in comps)


def test_build_report_shape_and_disclaimer():
    attempts = [_att(f"t{i}", "A", 0) for i in range(3)]
    rep = build_report(attempts)
    assert rep["n_attempts"] == 3
    assert "A" in rep["arms"]
    assert "Cliff's delta" in rep["disclaimer"]


def test_disclaimer_states_no_extrapolation():
    assert "不外推" in DISCLAIMER
    assert "0.47" in DISCLAIMER
    assert "p < 0.05" in DISCLAIMER


def test_render_table_contains_all_arms():
    attempts = [_att("t1", arm, 0) for arm in ("A", "B", "E", "A_prime")]
    table = render_table(summaries(attempts))
    for arm in ("A", "B", "E", "A_prime"):
        assert arm in table
    assert "完成率" in table
