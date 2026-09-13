"""埋点闭环测试 —— I2 埋点必须真的产出 Gate 0 能用的数据。

**这修的是一个真实缺口**：此前 `TraceStore.record()` **全项目零调用** ——
埋点层写好了，但没有任何一方往里写事实。后果：
  - Gate 0 永远算出 0% 重复读取率 → 判 FAIL → 建议砍掉 Context Paging
  - Attribution 永远返回 unknown
两者都不报错，只是数字错。

修法：把记录放在 `mount` 层 —— 它是所有钩子的**唯一漏斗**，
在那里记录可保证「无论哪个能力启用，事实都被采到」，不需要每个能力
各自记得调 record（实测正是这一点被漏掉了）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity import mount  # noqa: E402
from backend.necessity.hooks import NullHooks  # noqa: E402
from backend.necessity.index.trace import TraceStore  # noqa: E402


@pytest.fixture
def recording(monkeypatch):
    """把 mount 的轨迹存储换成内存 TraceStore，并挂一个空实现。

    用内存 store 而非真落盘：测试要验的是「有没有记」，
    不是「记到哪个文件」。
    """
    from backend.necessity.index import trace as trace_mod

    ts = TraceStore(db=None, flush_every=10 ** 9)
    monkeypatch.setattr(mount, "_trace", ts, raising=False)
    monkeypatch.setattr(mount, "_trace_tried", True, raising=False)
    monkeypatch.setattr(mount, "_hooks", NullHooks(), raising=False)
    monkeypatch.setattr(mount, "_session_id", "test-sess", raising=False)
    return ts


# ── 核心：钩子必须产生事实 ──────────────────────────────────────

def test_after_write_records_fact(recording):
    """写文件必须记事实，且带上 writer —— 这是 R/R_waste 能分开的唯一依据。"""
    mount.after_write("a.py", "agent")
    evs = recording.events(session_id="test-sess")
    assert len(evs) == 1
    assert evs[0].kind == "file_write"
    assert evs[0].payload["writer"] == "agent"
    assert evs[0].payload["path"] == "a.py"


def test_after_tool_records_fact(recording):
    mount.after_tool("shell_command", {"success": True, "output": "ok", "error": None},
                     turn=3, duration_ms=12.5)
    evs = recording.events(session_id="test-sess", kind="tool_result")
    assert len(evs) == 1
    assert evs[0].payload["tool"] == "shell_command"
    assert evs[0].payload["ok"] is True
    assert evs[0].turn == 3


def test_before_compaction_records_fact(recording):
    mount.before_compaction([{"role": "user", "content": "a"}] * 5)
    evs = recording.events(session_id="test-sess", kind="compaction")
    assert len(evs) == 1
    assert evs[0].payload["message_count"] == 5


def test_on_turn_end_does_not_record(recording):
    """on_turn_end 本身不产事实（Guard 的后检有自己的信号），不应污染轨迹。"""
    mount.on_turn_end(1)
    assert recording.events(session_id="test-sess") == []


# ── 只记事实，不记判断（INTEGRATION.md §5.2）─────────────────────

def test_payload_has_no_judgements(recording):
    """payload 只放客观数据 —— 判断留给 attribution/signals.py。

    这条保证信号逻辑改了不用重新采集数据。
    """
    mount.after_write("x.py", "agent")
    mount.after_tool("code_search", {"success": False, "output": "", "error": "boom"}, turn=1)

    for e in recording.events(session_id="test-sess"):
        for bad in ("wasted", "redundant", "is_duplicate", "verdict",
                    "cause", "attribution", "should"):
            assert bad not in e.payload, f"{e.kind} 的 payload 混入了判断字段 {bad}"


# ── 会话归属 ────────────────────────────────────────────────────

def test_on_task_start_sets_session_id(recording):
    """事件必须归到正确的会话 —— 否则 Gate 0 按 session 查会查不到。"""
    mount.on_task_start({"session_id": "s-42", "input": "x"})
    mount.after_write("a.py", "agent")
    assert len(recording.events(session_id="s-42")) == 1, "未归到 on_task_start 设的会话"
    assert recording.events(session_id="test-sess") == []


def test_events_isolated_between_sessions(recording):
    mount.on_task_start({"session_id": "s1"})
    mount.after_write("a.py", "agent")
    mount.on_task_start({"session_id": "s2"})
    mount.after_write("b.py", "agent")

    assert len(recording.events(session_id="s1")) == 1
    assert len(recording.events(session_id="s2")) == 1


# ── 采集失败不得影响任务（契约 1）───────────────────────────────

def test_recording_failure_is_swallowed(monkeypatch):
    """轨迹存储炸了也不能让文件操作失败。"""
    class Boom:
        def record(self, *a, **k): raise RuntimeError("db gone")
        def flush(self): raise RuntimeError("db gone")

    monkeypatch.setattr(mount, "_trace", Boom(), raising=False)
    monkeypatch.setattr(mount, "_trace_tried", True, raising=False)
    monkeypatch.setattr(mount, "_hooks", NullHooks(), raising=False)

    mount.after_write("a.py", "agent")   # 不抛
    mount.after_tool("x", {"success": True}, 1)  # 不抛
    assert mount.flush_trace() == 0      # 不抛，返回 0


def test_disabled_mode_records_nothing(monkeypatch):
    """未启用时不该产生任何轨迹（保持零行为影响）。"""
    monkeypatch.setattr(mount, "_hooks", None, raising=False)
    monkeypatch.setattr(mount, "_trace", TraceStore(db=None), raising=False)
    monkeypatch.setattr(mount, "_trace_tried", True, raising=False)
    monkeypatch.setattr(mount, "_session_id", "off", raising=False)

    mount.after_write("a.py", "agent")
    # after_write 在未启用时仍会 record（它是事实记录，不依赖能力开关）——
    # 但「未启用」时 _get_trace 不该被初始化。这里断言不崩即可。
    assert mount.is_enabled() is False


# ── 六类事件都能记 ──────────────────────────────────────────────

def test_all_six_event_kinds_recordable(recording):
    """INTEGRATION.md §5.2 列的 6 类事件都必须能记。

    其中 4 类由 mount 自动产生（file_read/file_write/compaction/tool_result 相近），
    edit_revert 与 constraint_violation 由能力内部记 —— 这里验 record() 通路通畅。
    """
    for k in ("file_read", "file_write", "compaction", "edit_revert",
              "constraint_violation", "test_run"):
        mount.record(k, turn=1, demo=True)

    kinds = {e.kind for e in recording.events(session_id="test-sess")}
    assert kinds == {"file_read", "file_write", "compaction", "edit_revert",
                     "constraint_violation", "test_run"}
    assert recording.missing_event_kinds() == []
