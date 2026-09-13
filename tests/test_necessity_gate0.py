"""Gate 0 测量逻辑测试。

重点验证「浪费 vs 合法」的分类边界 —— 分错会让 Gate 0 的结论整个反过来：
把合法重读算成浪费 → 虚高 → 白做 2 周；
把浪费算成合法 → 虚低 → 误砍一个有效能力。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.index.trace import AgentEvent, TraceStore  # noqa: E402
from backend.necessity.eval.gate0 import (  # noqa: E402
    THRESHOLD_GO,
    THRESHOLD_MARGINAL,
    classify_reads,
)


def ev(kind, turn, **payload):
    return AgentEvent(session_id="s", kind=kind, turn=turn, payload=payload)


# ── 合法重读（不算浪费）─────────────────────────────────────────

def test_first_read_is_legit():
    r = classify_reads([ev("file_read", 1, path="a.py")])
    assert r.total_reads == 1
    assert r.waste_reads == 0


def test_reread_after_write_is_legit():
    """编辑后验证改动是否落地 —— CONTEXT_PAGING.md §2.3 明确列为合法。"""
    r = classify_reads([
        ev("file_read", 1, path="a.py"),
        ev("file_write", 2, path="a.py", writer="agent"),
        ev("file_read", 3, path="a.py"),
    ])
    assert r.total_reads == 2
    assert r.waste_reads == 0, "改了之后重读是合法验证，不是浪费"


def test_reread_after_external_change_is_legit():
    """外部工具（git checkout / formatter）改了文件，重读合法。"""
    r = classify_reads([
        ev("file_read", 1, path="a.py"),
        ev("file_write", 2, path="a.py", writer="other"),
        ev("file_read", 3, path="a.py"),
    ])
    assert r.waste_reads == 0


# ── 浪费重读 ────────────────────────────────────────────────────

def test_same_turn_reread_is_waste():
    r = classify_reads([
        ev("file_read", 1, path="a.py"),
        ev("file_read", 1, path="a.py"),
    ])
    assert r.waste_reads == 1
    assert "same_turn_reread" in r.per_file["a.py"]["reasons"]


def test_reread_after_compaction_without_change_is_waste():
    """压缩后遗忘式重读 —— 这正是 Context Paging 要解决的问题。"""
    r = classify_reads([
        ev("file_read", 1, path="a.py"),
        ev("compaction", 2, token_before=9000, token_after=3000),
        ev("file_read", 3, path="a.py"),
    ])
    assert r.total_reads == 2
    assert r.waste_reads == 1
    assert "forgotten_after_compaction" in r.per_file["a.py"]["reasons"]
    assert r.compactions == 1


def test_reread_across_turns_without_compaction_is_legit():
    """跨轮重读、没压缩、没改动 —— 按当前定义不计入浪费。

    这条是刻意的保守选择：无法区分『忘了』与『多处引用需要再看』，
    宁可低估也不虚高（宁可漏判，不可误砍能力）。
    """
    r = classify_reads([
        ev("file_read", 1, path="a.py"),
        ev("file_read", 5, path="a.py"),
    ])
    assert r.waste_reads == 0


def test_write_after_compaction_makes_reread_legit():
    """压缩之后文件又被改过 —— 重读是合法验证。"""
    r = classify_reads([
        ev("file_read", 1, path="a.py"),
        ev("compaction", 2),
        ev("file_write", 3, path="a.py", writer="agent"),
        ev("file_read", 4, path="a.py"),
    ])
    assert r.waste_reads == 0


# ── 比率与判定 ──────────────────────────────────────────────────

def test_waste_ratio_and_thresholds():
    events = []
    # 10 次首读（合法）
    for i in range(10):
        events.append(ev("file_read", 1, path=f"f{i}.py"))
    # 制造压缩后遗忘式重读：3 次
    events.append(ev("compaction", 2))
    for i in range(3):
        events.append(ev("file_read", 3, path=f"f{i}.py"))

    r = classify_reads(events)
    assert r.total_reads == 13
    assert r.waste_reads == 3
    assert abs(r.waste_ratio - 3 / 13) < 1e-9

    verdict, _ = r.verdict()
    assert verdict in ("MARGINAL", "PASS")


def test_verdict_pass_above_15pct():
    events = [ev("file_read", 1, path="a.py")]
    events.append(ev("compaction", 2))
    for _ in range(3):
        events.append(ev("file_read", 3, path="a.py"))
    r = classify_reads(events)
    assert r.waste_ratio > THRESHOLD_GO
    assert r.verdict()[0] == "PASS"


def test_verdict_fail_below_5pct():
    """全是首读 —— 说明现状没有重复读问题，应砍掉 Context Paging。"""
    events = [ev("file_read", 1, path=f"f{i}.py") for i in range(100)]
    r = classify_reads(events)
    assert r.waste_ratio < THRESHOLD_MARGINAL
    verdict, action = r.verdict()
    assert verdict == "FAIL"
    assert "省下 2 周" in action


def test_empty_events_is_zero_not_crash():
    r = classify_reads([])
    assert r.total_reads == 0
    assert r.waste_ratio == 0.0
    assert r.verdict()[0] == "FAIL"


def test_per_file_breakdown():
    r = classify_reads([
        ev("file_read", 1, path="a.py"),
        ev("file_read", 1, path="a.py"),   # waste
        ev("file_read", 1, path="b.py"),
    ])
    assert r.per_file["a.py"]["reads"] == 2
    assert r.per_file["a.py"]["waste"] == 1
    assert r.per_file["b.py"]["waste"] == 0


def test_cli_reports_missing_instrumentation(capsys):
    """没有任何轨迹时必须明确告知「埋点未生效」，而不是算个 0% 说没价值。

    否则会把「采集没做」误判成「不存在重复读问题」然后误砍能力。
    """
    from backend.necessity.eval.gate0 import main

    rc = main([])
    out = capsys.readouterr().out
    assert rc == 2
    assert "埋点未生效" in out or "没有采集到" in out
    assert "缺失的事件类别" in out
