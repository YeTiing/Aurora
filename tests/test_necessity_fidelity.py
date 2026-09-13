"""保真度检查 —— DIFF_REDUCER.md §8.4 的成功判据。

文档原话：「保真度 < 100%（说明算法会破坏功能，**必须修**）」。

它守护的东西很关键：没有保真度，**冗余率这个指标可以被算法作弊**——
删得越激进冗余率越好看，但功能已经坏了。这里验证它能识别出这种破坏。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.reduce.fidelity import (  # noqa: E402
    ERROR,
    FAIL,
    PASS,
    FidelityResult,
    attach_to_report,
    check_fidelity,
)
from backend.necessity.reduce.report import build_report  # noqa: E402
from backend.necessity.reduce.search import minimize_necessary  # noqa: E402
from backend.necessity.reduce.split import Hunk  # noqa: E402


def mk(i, added=5, removed=2):
    return Hunk(id=f"h{i}", file="a.py", old_start=i + 1, new_start=i + 1,
                old_count=1, new_count=1, added=added, removed=removed)


# ── 基本语义 ────────────────────────────────────────────────────

def test_fidelity_pass_when_subset_still_passes():
    r = check_fidelity(lambda s: PASS, [mk(0)], baseline_result=PASS)
    assert r.checked == 1 and r.preserved == 1
    assert r.fidelity == 1.0 and r.ok is True


def test_fidelity_fails_when_minimization_broke_function():
    """**核心用例**：最小化后测试失败 = 删掉了必要的改动。"""
    r = check_fidelity(lambda s: FAIL, [mk(0)], baseline_result=PASS)
    assert r.ok is False
    assert r.fidelity == 0.0
    assert r.failures, "必须记录失败详情以便定位算法问题"
    assert "删掉了必要的改动" in r.failures[0]["reason"]


def test_fidelity_records_subset_ids_on_failure():
    r = check_fidelity(lambda s: FAIL, [mk(0), mk(1)], baseline_result=PASS)
    assert r.failures[0]["subset_ids"] == ["h0", "h1"]


def test_fidelity_handles_flat_and_grouped_subset():
    flat = check_fidelity(lambda s: FAIL, [mk(0)], baseline_result=PASS)
    grouped = check_fidelity(lambda s: FAIL, [[mk(0), mk(1)]], baseline_result=PASS)
    assert flat.failures[0]["subset_ids"] == ["h0"]
    assert grouped.failures[0]["subset_ids"] == ["h0", "h1"]


# ── 边界：基线不通过 / 无法判定 ─────────────────────────────────

def test_fidelity_skips_when_baseline_not_pass():
    """§7 边界 5：基线就不是 pass 时，保真度无从谈起。"""
    r = check_fidelity(lambda s: PASS, [mk(0)], baseline_result=FAIL)
    assert r.checked == 0
    assert r.errors and "基线测试不是 pass" in r.errors[0]
    # 没有样本：ok=True（未被证明为坏）但 verified=False（没检查过）——
    # 两者必须分开，否则「没检查」会被当成「保真度 100%」
    assert r.ok is True
    assert r.verified is False


def test_fidelity_treats_error_as_not_ok():
    """无法判定时**保守记为不达标** —— 未知不等于通过。"""
    r = check_fidelity(lambda s: ERROR, [mk(0)], baseline_result=PASS)
    assert r.ok is False
    assert r.errors and "无法判定" in r.errors[0]


def test_fidelity_handles_runner_exception():
    def boom(s): raise RuntimeError("sandbox crashed")
    r = check_fidelity(boom, [mk(0)], baseline_result=PASS)
    assert r.ok is False
    assert "sandbox crashed" in r.errors[0]


def test_fidelity_no_samples_is_ok():
    """没有检查样本时不报失败（避免在无数据时虚报问题）。"""
    r = check_fidelity(lambda s: PASS, [], baseline_result=PASS)
    assert r.ok is True
    assert r.fidelity == 1.0


# ── 与真实 ddmin 串联 ───────────────────────────────────────────

def test_fidelity_with_real_ddmin_clean_case():
    """未被破坏的最小化 -> 保真度 100%。"""
    hs = [mk(0), mk(1), mk(2)]

    def runner(subset):
        ids = {h.id for g in subset for h in g} if subset and isinstance(subset[0], list) \
            else {h.id for h in subset}
        return PASS if "h2" not in ids else FAIL

    groups = [[h] for h in hs]
    res = minimize_necessary(groups, runner)
    fid = check_fidelity(runner, res.hunks, baseline_result=PASS)
    assert fid.ok is True, "正常最小化不该被判定为破坏功能"


def test_fidelity_catches_the_cheating_scenario():
    """**这是保真度存在的理由**：算法删过头时，冗余率好看但功能已坏。

    构造：删掉 h0 后测试失败（h0 是必要的），但搜索误把它当冗余删了。
    保真度必须抓到 —— 否则冗余率会被当成「优化成功」。
    """
    hs = [mk(0), mk(1)]

    def runner(subset):
        ids = {h.id for g in subset for h in g} if subset and isinstance(subset[0], list) \
            else {h.id for h in subset}
        # h0 必要：缺了就 fail
        return PASS if "h0" in ids else FAIL

    groups = [[h] for h in hs]
    res = minimize_necessary(groups, runner)
    fid = check_fidelity(runner, res.hunks, baseline_result=PASS)

    # ddmin 是正确算法，应保住 h0 -> 保真
    assert fid.ok is True
    assert "h0" in {h.id for h in res.hunks}


# ── 写进报告 ────────────────────────────────────────────────────

def test_attach_adds_metrics_and_warns_on_failure():
    hs = [mk(0), mk(1)]
    res = minimize_necessary([[h] for h in hs], lambda s: PASS)
    rep = build_report(hs, res, task_id="t")

    bad = FidelityResult(checked=1, preserved=0,
                         failures=[{"reason": "删掉了必要的改动", "subset_ids": ["h0"]}])
    attach_to_report(rep, bad)

    assert rep.metrics["fidelity"] == 0.0
    assert rep.metrics["fidelity_ok"] is False
    assert any("保真度不达标" in n for n in rep.notes)
    assert any("冗余率不可用" in n for n in rep.notes), \
        "不达标必须明确说明冗余率不可用 —— 否则读者会继续引用那个数字"


def test_attach_quiet_when_fidelity_ok():
    hs = [mk(0)]
    res = minimize_necessary([[h] for h in hs], lambda s: PASS)
    rep = build_report(hs, res, task_id="t")

    attach_to_report(rep, FidelityResult(checked=1, preserved=1))
    assert rep.metrics["fidelity"] == 1.0
    assert rep.metrics["fidelity_ok"] is True
    assert not any("保真度" in n for n in rep.notes), "达标时不该有噪音告警"


def test_attach_warns_on_unverifiable():
    """无法确认时也要提示 —— 未知不等于通过。"""
    hs = [mk(0)]
    res = minimize_necessary([[h] for h in hs], lambda s: PASS)
    rep = build_report(hs, res, task_id="t")

    attach_to_report(rep, FidelityResult(checked=0, errors=["基线测试不是 pass"]))
    assert rep.metrics["fidelity_verified"] is False
    assert any("保真度无法确认" in n for n in rep.notes)
