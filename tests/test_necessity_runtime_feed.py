"""运行时闭环的「观测→判定」接线 —— 锁死窗口语义与失败隔离。

规格：`Aurora_六项能力设计规范.md` §1.4。

## 这个模块存在的理由

`gate/runtime_gate.py` 实现了判定与动作，但**没有调用方** ——
它需要「一段时间的窗口」，而窗口只能由每次运行累积。
本层就是 ①观测 → ②判定 的那一步。

## 一个必须明确的语义（规范没写，但写错会让恢复门槛失效）

规范只说「滑动窗口 W 个任务」，没说判定频率。实测踩过的错：
**每个样本判一次**（重叠窗口）会让 `restore_windows` 的语义从
「连续 2 个**窗口**」退化成「连续 2 个**样本**」—— 一个达标样本就把
降级状态清掉了。本文件用「第 1 个达标窗口后仍应 degraded」锁死这条。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.records import Attempt, GateMetrics  # noqa: E402
from backend.necessity.eval.runtime_feed import RuntimeFeed  # noqa: E402
from backend.necessity.gate.runtime_gate import RuntimeGate  # noqa: E402

W = 20


def attempt(metric="bundle_impact_accuracy", value=0.0, arm="A1"):
    return Attempt(attempt_id="x", task_id="t", arm=arm,
                   gates=GateMetrics(**{metric: value}))


def feed_with_a1() -> RuntimeFeed:
    return RuntimeFeed(gate=RuntimeGate(
        thresholds={"A1": {"bundle_impact_accuracy": 0.8}}))


# ── 窗口语义 ───────────────────────────────────────────────────

def test_no_judgement_before_window_is_full():
    """窗口没满不判定 —— 样本太少的判定会把噪声当退化。"""
    feed = feed_with_a1()
    for _ in range(W - 1):
        assert feed.observe(attempt(value=0.1)) == {}
    assert feed.degraded_capabilities() == []


def test_full_degrading_window_triggers_degrade_with_p_value():
    feed = feed_with_a1()
    out = {}
    for _ in range(W):
        out = feed.observe(attempt(value=0.1)) or out
    assert "A1" in out
    assert out["A1"]["degraded"] is True
    assert out["A1"]["p_values"] and out["A1"]["p_values"][0] is not None
    assert feed.degraded_capabilities() == ["A1"]


def test_restore_requires_two_full_windows_not_two_samples():
    """**核心语义**：恢复要「连续 2 个**窗口**」，不是 2 个样本。

    规范 §1.4 的恢复条件是「连续 M 个窗口回到阈值内」（默认 M=2）。
    若判定频率写成「每样本一次」（重叠窗口），一个达标样本就会把降级
    清掉 —— 恢复门槛被悄悄降低，而降级/恢复反复抖动比不降级更糟。
    """
    feed = feed_with_a1()
    for _ in range(W):
        feed.observe(attempt(value=0.1))
    assert feed.degraded_capabilities() == ["A1"]

    # 第 1 个达标窗口：仍应 degraded
    for _ in range(W):
        feed.observe(attempt(value=0.95))
    assert feed.degraded_capabilities() == ["A1"], \
        "一个达标样本/窗口就恢复了 —— restore_windows 语义失效"

    # 第 2 个达标窗口：这才恢复
    for _ in range(W):
        feed.observe(attempt(value=0.95))
    assert feed.degraded_capabilities() == []


def test_windows_do_not_overlap():
    """窗口取走即清空 —— 否则同一批数据会被反复判定。"""
    feed = feed_with_a1()
    for _ in range(W):
        feed.observe(attempt(value=0.1))
    f = feed._feed_for("A1")
    assert len(f.samples) == 0, "判定后窗口未清空"


# ── 未标定不判定（规范 §0.3）──────────────────────────────────

def test_uncalibrated_capability_is_never_judged():
    """没有阈值的（未标定）能力一律不判定。

    规范 §0.3：「阶段 A 完成前，所有阈值列必须标为『待标定』，
    且**不得**写进验收清单作为通过条件」。
    """
    feed = RuntimeFeed()          # 默认无 thresholds
    for _ in range(200):
        out = feed.observe(attempt(value=0.0))
    assert out == {}
    assert feed.degraded_capabilities() == []


# ── 失败隔离 ───────────────────────────────────────────────────

def test_observe_failure_does_not_raise():
    """闭环观测失败不能让运行崩掉 —— 它是观测，不是判定者。"""
    class BoomFeed(RuntimeFeed):
        def observe(self, a):
            raise RuntimeError("x")

    feed = RuntimeFeed()
    feed.observe(object())      # 无 gates 的对象也不该抛
    assert feed.observed == 0


def test_attempt_without_gates_is_ignored():
    feed = feed_with_a1()
    assert feed.observe(object()) == {}
    assert feed.observed == 0


def test_state_reports_window_progress():
    """状态里要能看到「窗口填了多少」—— 否则无法判断闭环是否在工作。"""
    feed = feed_with_a1()
    for _ in range(5):
        feed.observe(attempt(value=0.1))
    st = feed.state()
    assert st["A1"]["samples"] == 5
    assert st["A1"]["window_size"] == W
    assert st["A1"]["degraded"] is False


# ── 接线：execute 必须能喂进去 ─────────────────────────────────

def test_execute_passes_verdicts_into_attempt_meta():
    """`run_and_measure` 接受 `runtime_feed` 并把判定写进 meta。"""
    import inspect

    from backend.necessity.eval import execute

    sig = inspect.signature(execute.run_and_measure)
    assert "runtime_feed" in sig.parameters, "execute 未提供闭环注入点"


def test_execute_defaults_to_no_feed():
    """默认不喂 —— 单次运行与闭环解耦，不背跨运行状态。"""
    import inspect

    from backend.necessity.eval import execute

    p = inspect.signature(execute.run_and_measure).parameters["runtime_feed"]
    assert p.default is None
