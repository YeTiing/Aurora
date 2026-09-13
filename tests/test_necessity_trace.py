"""轨迹采集测试 —— 只记事实、不丢事实、不阻塞任务。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.index.trace import (  # noqa: E402
    REQUIRED_EVENTS,
    AgentEvent,
    TraceStore,
    get_trace,
    reset_trace,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_trace()
    yield
    reset_trace()


def test_records_all_required_event_kinds():
    """INTEGRATION.md §5.2 列出的 6 类事件都必须能记录。

    这 6 类正是 Aurora 的 sse_events 缺失的部分（实测确认 62 个事件里
    没有 file_read / file_write / compaction）。
    """
    t = TraceStore()
    t.record("s1", "file_read", turn=1, path="a.py", lines=10, hash="h1")
    t.record("s1", "file_write", turn=1, path="a.py", added=3, removed=1)
    t.record("s1", "compaction", turn=2, token_before=9000, token_after=3000)
    t.record("s1", "edit_revert", turn=3, path="a.py")
    t.record("s1", "constraint_violation", turn=4, constraint_id="c1")
    t.record("s1", "test_run", turn=5, suite="tests/", result="fail")

    assert t.missing_event_kinds() == [], "六类事件应全部被覆盖"
    assert len(t.events(session_id="s1")) == 6


def test_payload_stores_facts_not_judgements():
    """payload 只放客观数据 —— 判断属于 signals.py。

    这条保证信号逻辑改了不用重新采集数据。
    """
    t = TraceStore()
    t.record("s", "file_read", turn=1, path="x.py", lines=42, hash="abc")

    e = t.events(session_id="s")[0]
    assert e.payload["path"] == "x.py"
    assert e.payload["lines"] == 42
    assert e.payload["hash"] == "abc"
    # 不应出现任何判断性字段
    for bad in ("wasted", "redundant", "is_duplicate", "verdict", "cause"):
        assert bad not in e.payload, f"payload 不应含判断字段 {bad}"


def test_events_filtered_by_session_and_kind():
    t = TraceStore()
    t.record("a", "file_read", turn=1)
    t.record("b", "file_read", turn=1)
    t.record("a", "file_write", turn=2)

    assert len(t.events(session_id="a")) == 2
    assert len(t.events(session_id="b")) == 1
    assert len(t.events(kind="file_read")) == 2
    assert len(t.events(session_id="a", kind="file_read")) == 1


def test_flush_batches_to_db():
    """攒批落库 —— 主循环路径上不能每个事件都写盘。"""

    class FakeDB:
        def __init__(self):
            self.rows = []

        def append_agent_events(self, rows):
            self.rows.extend(rows)

        def query_agent_events(self, session_id="", kind=""):
            return []

    db = FakeDB()
    t = TraceStore(db=db, flush_every=3)
    for i in range(3):
        t.record("s", "file_read", turn=i)
    assert len(db.rows) == 3, "达到阈值应自动落库"

    # 未达阈值的留在缓冲区
    t.record("s", "file_write", turn=9)
    assert len(db.rows) == 3
    assert t.flush() == 1
    assert len(db.rows) == 4


def test_db_failure_does_not_lose_facts():
    """落库失败时数据必须放回缓冲，不能丢。"""

    class BrokenDB:
        def append_agent_events(self, rows):
            raise RuntimeError("disk full")

        def query_agent_events(self, session_id="", kind=""):
            return []

    t = TraceStore(db=BrokenDB(), flush_every=2)
    t.record("s", "file_read", turn=1)
    t.record("s", "file_read", turn=2)

    # 落库失败但事实仍在
    assert len(t.events(session_id="s")) == 2
    assert t.stats()["dropped"] > 0, "失败次数要可观测"


def test_record_never_raises():
    """采集故障不得阻塞任务（契约 1）。"""
    t = TraceStore()

    class BadPayload:
        def __repr__(self):
            raise RuntimeError("boom")

    t.record("s", "file_read", turn=1, weird=BadPayload())  # 不抛


def test_missing_event_kinds_reports_gaps():
    """未采齐的类别要能报出来 —— Gate 6 的『信号覆盖不足』判断依据。"""
    t = TraceStore()
    t.record("s", "file_read", turn=1)

    missing = t.missing_event_kinds()
    assert "file_read" not in missing
    assert set(missing) == set(REQUIRED_EVENTS) - {"file_read"}


def test_singleton_is_shared():
    a = get_trace()
    b = get_trace()
    assert a is b

    a.record("s", "file_read", turn=1)
    assert len(get_trace().events()) == 1


def test_singleton_binds_db_lazily():
    """单例允许后补 db（宿主挂载顺序可能晚于首次取用）。"""

    class FakeDB:
        def __init__(self):
            self.rows = []

        def append_agent_events(self, rows):
            self.rows.extend(rows)

        def query_agent_events(self, session_id="", kind=""):
            return []

    t = get_trace()
    assert t._db is None
    db = FakeDB()
    t2 = get_trace(db=db)
    assert t2._db is db


def test_event_dataclass_has_timestamp():
    e = AgentEvent(session_id="s", kind="file_read")
    assert e.ts > 0
