"""轨迹持久化的端到端闭环 —— 锁死一个曾让 Gate 0 永久失效的缺陷。

缺陷链（两个独立 agent 各自发现）：
  1. Store 从未实现 append/query_agent_events，schema 里也没有 agent_event 表
  2. trace.TraceStore.events() 用 `except Exception: pass` 吞掉了
     「payload 已是 dict 却又 json.loads」的 TypeError

后果：事件**写进去了但读不回来**，且不报错。表现为
  - Attribution 永远返回 unknown
  - Gate 0 永远算出 0% 重复读取率 → 判 FAIL
    → 等于建议砍掉一个可能有效的功能（文档说这能省 2 周）

这条链路必须端到端验证，单测任一环都发现不了。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.index.store import Store  # noqa: E402
from backend.necessity.index.trace import REQUIRED_EVENTS, TraceStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "trace.db"))


def test_store_exposes_trace_methods(store):
    """Store 必须实现 trace.py 依赖的两个方法。

    缺了它们时 trace.py 的异常被吞掉，表现为「静默无事件」。
    """
    assert hasattr(store, "append_agent_events")
    assert hasattr(store, "query_agent_events")


def test_agent_event_table_exists(store):
    """schema 里必须有 agent_event 表 —— 曾被遗漏。"""
    with store._read() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_event'"
        ).fetchone()
    assert row is not None, "agent_event 表不存在（trace 会静默失效）"


def test_write_then_read_roundtrip(store):
    """**核心闭环**：写进去必须能读回来。"""
    ts = TraceStore(db=store, flush_every=1)
    ts.record("s1", "file_read", turn=1, path="a.py", lines=10)
    ts.record("s1", "compaction", turn=2, token_before=9000, token_after=3000)
    ts.flush()

    got = ts.events(session_id="s1")
    assert len(got) == 2, f"写入 2 条却读回 {len(got)} 条"
    assert {e.kind for e in got} == {"file_read", "compaction"}


def test_payload_roundtrips_as_dict(store):
    """payload 读回来必须是 dict，且内容不丢。

    这正是曾被 json.loads(dict) 抛 TypeError 的地方。
    """
    ts = TraceStore(db=store, flush_every=1)
    ts.record("s1", "file_write", turn=3, path="x.py", added=5, removed=2)
    ts.flush()

    e = ts.events(session_id="s1")[0]
    assert isinstance(e.payload, dict)
    assert e.payload["path"] == "x.py"
    assert e.payload["added"] == 5


def test_events_sorted_by_turn(store):
    """必须按 turn 排序 —— Attribution 的时序信号依赖它。

    「压缩**之后**又重读了压缩前读过的文件」这类判定，乱序会失效或反向。
    """
    ts = TraceStore(db=store, flush_every=1)
    for turn in (5, 1, 3, 2, 4):
        ts.record("s1", "file_read", turn=turn, path=f"f{turn}.py")
    ts.flush()

    turns = [e.turn for e in ts.events(session_id="s1")]
    assert turns == sorted(turns), f"未按 turn 排序: {turns}"


def test_kind_filter(store):
    ts = TraceStore(db=store, flush_every=1)
    ts.record("s1", "file_read", turn=1, path="a.py")
    ts.record("s1", "file_write", turn=2, path="a.py")
    ts.flush()

    reads = ts.events(session_id="s1", kind="file_read")
    assert len(reads) == 1 and reads[0].kind == "file_read"


def test_all_six_event_kinds_roundtrip(store):
    """6 类必需事件都必须能存能取（INTEGRATION.md §5.2）。"""
    ts = TraceStore(db=store, flush_every=1)
    for i, k in enumerate(REQUIRED_EVENTS):
        ts.record("s1", k, turn=i)
    ts.flush()

    got = ts.events(session_id="s1")
    assert len(got) == len(REQUIRED_EVENTS)
    assert ts.missing_event_kinds() == []


def test_session_isolation(store):
    ts = TraceStore(db=store, flush_every=1)
    ts.record("a", "file_read", turn=1)
    ts.record("b", "file_read", turn=1)
    ts.flush()

    assert len(ts.events(session_id="a")) == 1
    assert len(ts.events(session_id="b")) == 1


def test_gate0_works_on_persisted_events(store):
    """**端到端**：Gate 0 必须能从持久化的事件里算出真实比率。

    修之前这里恒为 0%（读不到事件）→ 判 FAIL → 误判「不需要 Context Paging」。
    """
    from backend.necessity.eval.gate0 import run_gate0

    ts = TraceStore(db=store, flush_every=1)
    # 首读 + 压缩 + 遗忘式重读
    ts.record("s1", "file_read", turn=1, path="a.py")
    ts.record("s1", "compaction", turn=2, token_before=9000, token_after=3000)
    ts.record("s1", "file_read", turn=3, path="a.py")
    ts.flush()

    r = run_gate0(ts, "s1")
    assert r.total_reads == 2, "Gate 0 读不到事件（持久化链路断了）"
    assert r.waste_reads == 1
    assert abs(r.waste_ratio - 0.5) < 1e-9


def test_db_write_failure_does_not_lose_events():
    """落库失败时事件必须留在内存，不丢事实。"""

    class BrokenStore:
        def append_agent_events(self, rows): raise RuntimeError("disk full")
        def query_agent_events(self, session_id="", kind=""): raise RuntimeError("disk full")

    ts = TraceStore(db=BrokenStore(), flush_every=1)
    ts.record("s1", "file_read", turn=1, path="a.py")
    # 落库失败但缓冲里还在
    assert len(ts.events(session_id="s1")) >= 1


def test_query_failure_is_logged_not_silent(store, caplog):
    """查询失败必须留下日志 —— 曾用 `except: pass` 静默吞掉。

    静默的后果是伪装成「没有浪费行为」，比报错更危险。
    """
    import logging

    class HalfBroken:
        def append_agent_events(self, rows): return len(rows)
        def query_agent_events(self, session_id="", kind=""):
            raise RuntimeError("schema mismatch")

    ts = TraceStore(db=HalfBroken(), flush_every=1)
    ts.record("s1", "file_read", turn=1)
    with caplog.at_level(logging.WARNING, logger="necessity.index.trace"):
        ts.events(session_id="s1")
    assert any("query_agent_events failed" in r.message for r in caplog.records), \
        "查询失败被静默吞掉了"
