# -*- coding: utf-8 -*-
"""Tests for 6 new modules: LSP tool, verify_plan, tool_metrics, transcript_index, hooks_system, task_monitor."""
import sys, os, pytest, time, json, tempfile
sys.path.insert(0, r"D:\codex_Projects\Aurora")

# ── LSP Tool ──────────────────────────────────────────────────

def test_lsp_tool_spec():
    from backend.tools.lsp_tool import LSP_TOOL_SPEC
    assert LSP_TOOL_SPEC["name"] == "lsp"
    assert "action" in str(LSP_TOOL_SPEC["parameters"])

@pytest.mark.asyncio
async def test_lsp_handler_no_file():
    from backend.tools.lsp_tool import lsp_handler
    result = await lsp_handler({"action": "diagnostics", "filepath": ""})
    assert not result["success"]
    assert "required" in result["error"].lower()

@pytest.mark.asyncio
async def test_lsp_handler_file_not_found():
    from backend.tools.lsp_tool import lsp_handler
    result = await lsp_handler({"action": "diagnostics", "filepath": "/nonexistent/test.xyz"})
    assert not result["success"]

# ── Plan Verifier ─────────────────────────────────────────────

def test_verifier_spec():
    from backend.tools.verify_plan import VERIFY_TOOL_SPEC
    assert VERIFY_TOOL_SPEC["name"] == "verify_plan"

def test_verifier_file_check(tmp_path):
    from backend.tools.verify_plan import PlanVerifier
    v = PlanVerifier(str(tmp_path))
    # Create a file
    tf = tmp_path / "test.py"
    tf.write_text("print('hello')")
    result = v.verify("create test.py", expected_files=["test.py"])
    assert result["passed"]
    assert len(result["checks"]) >= 1

def test_verifier_missing_file(tmp_path):
    from backend.tools.verify_plan import PlanVerifier
    v = PlanVerifier(str(tmp_path))
    result = v.verify("create missing.py", expected_files=["missing.py"])
    assert not result["passed"]

# ── Tool Metrics ──────────────────────────────────────────────

def test_metrics_record():
    from backend.tools.tool_metrics import ToolMetrics
    m = ToolMetrics()
    m.record_start("test.tool", {"x": 1})
    m.record_end("test.tool", True, "", 100)
    stats = m.get_stats("test.tool")
    assert stats["calls"] == 1
    assert stats["success"] == 1

def test_metrics_summary():
    from backend.tools.tool_metrics import ToolMetrics
    m = ToolMetrics()
    m.record_start("tool_a", {})
    m.record_end("tool_a", True, "", 50)
    m.record_start("tool_b", {})
    m.record_end("tool_b", False, "err", 0)
    summary = m.get_summary()
    assert summary["total_calls"] == 2
    assert summary["total_success"] == 1
    assert summary["total_failure"] == 1

def test_metrics_latency():
    from backend.tools.tool_metrics import ToolMetrics
    m = ToolMetrics()
    for i in range(10):
        m.record_start("slow", {})
        time.sleep(0.01)
        m.record_end("slow", True, "", 10)
    stats = m.get_stats("slow")
    assert stats["calls"] == 10
    assert stats["avg_ms"] > 0

def test_metrics_recent():
    from backend.tools.tool_metrics import ToolMetrics
    m = ToolMetrics()
    m.record_start("t1", {})
    m.record_end("t1", True, "", 10)
    recent = m.get_recent(5)
    assert len(recent) == 1

def test_metrics_singleton():
    from backend.tools.tool_metrics import get_metrics
    m1 = get_metrics()
    m2 = get_metrics()
    assert m1 is m2

# ── Transcript Index ──────────────────────────────────────────

def test_transcript_index_empty():
    from backend.transcript_index import TranscriptIndex
    with tempfile.TemporaryDirectory() as d:
        idx = TranscriptIndex(sessions_dir=d)
        count = idx.build()
        assert count == 0

def test_transcript_index_build(tmp_path):
    from backend.transcript_index import TranscriptIndex
    # Create a fake session file
    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir()
    sf = sess_dir / "rollout-test123.jsonl"
    sf.write_text('{"type":"session_meta","timestamp":"2024-01-01T00:00:00","payload":{"cwd":"/test"}}\n{"type":"tool_call","timestamp":"2024-01-01T00:01:00","payload":{"output":"hello world"}}\n', encoding='utf-8')
    idx = TranscriptIndex(sessions_dir=str(sess_dir))
    count = idx.build()
    assert count == 1
    sessions = idx.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "test123"

def test_transcript_search(tmp_path):
    from backend.transcript_index import TranscriptIndex
    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir()
    sf = sess_dir / "rollout-abc.jsonl"
    sf.write_text('{"type":"tool_call","timestamp":"2024-01-01T00:01:00","payload":{"output":"search term here"}}\n', encoding='utf-8')
    idx = TranscriptIndex(sessions_dir=str(sess_dir))
    idx.build()
    hits = idx.search("search term")
    assert len(hits) == 1
    hits2 = idx.search("nonexistent")
    assert len(hits2) == 0

def test_transcript_stats(tmp_path):
    from backend.transcript_index import TranscriptIndex
    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir()
    sf = sess_dir / "rollout-x.jsonl"
    sf.write_text('{"type":"test","timestamp":"2024-01-01T00:00:00"}\n{"type":"test2","timestamp":"2024-01-01T00:01:00"}\n', encoding='utf-8')
    idx = TranscriptIndex(sessions_dir=str(sess_dir))
    idx.build()
    stats = idx.stats()
    assert stats["sessions"] == 1
    assert stats["total_lines"] == 2

# ── Hooks System ──────────────────────────────────────────────

def test_hook_registry():
    from backend.hooks_system import HookRegistry, HookPoint, HookContext
    registry = HookRegistry()
    results = []
    def my_hook(ctx):
        results.append("called")
        return True
    hid = registry.register(HookPoint.POST_MODEL_OUTPUT, my_hook)
    assert hid.startswith("hook_post_model_output")

def test_hook_stats():
    from backend.hooks_system import HookRegistry, HookPoint, HookContext, HookResult
    registry = HookRegistry()
    registry.register(HookPoint.PRE_TOOL_EXEC, lambda ctx: HookResult(allow=True))
    stats = registry.stats()
    assert len(stats) >= 1, f"Expected hooks stats, got: {list(stats.keys())}"

@pytest.mark.asyncio
async def test_hook_run():
    from backend.hooks_system import HookRegistry, HookPoint, HookContext
    registry = HookRegistry()
    called = []
    registry.register(HookPoint.PRE_TOOL_EXEC, lambda ctx: called.append(ctx.tool_name) or True)
    ctx = HookContext(hook_point=HookPoint.PRE_TOOL_EXEC, tool_name="test_tool")
    results = await registry.run_hooks(HookPoint.PRE_TOOL_EXEC, ctx)
    assert len(called) == 1
    assert called[0] == "test_tool"

def test_builtin_hooks_registered():
    from backend.hooks_system import get_hook_registry
    registry = get_hook_registry()
    stats = registry.stats()
    assert len(stats) >= 2  # At least pre_tool_exec and post_tool_exec

# ── Task Monitor ──────────────────────────────────────────────

def test_monitor_add_target():
    from backend.task_monitor import BackgroundMonitor
    m = BackgroundMonitor()
    tid = m.add_target("test-watch", ".", "file", interval_sec=999)
    assert len(m.get_targets()) == 1
    targets = m.get_targets()
    assert targets[0]["name"] == "test-watch"
    m.remove_target(tid)
    assert len(m.get_targets()) == 0

@pytest.mark.asyncio
async def test_monitor_file_watch(tmp_path):
    from backend.task_monitor import BackgroundMonitor
    m = BackgroundMonitor()
    tf = tmp_path / "watched.txt"
    tf.write_text("initial content")
    tid = m.add_target("test", str(tf), "file", interval_sec=0)
    events = await m.check_now()
    assert len(events) == 0
    tf.write_text("changed content")
    events2 = await m.check_now()
    assert len(events2) == 1
    assert events2[0].change_type == "modified"

def test_monitor_events():
    from backend.task_monitor import BackgroundMonitor, ChangeEvent
    m = BackgroundMonitor()
    events = m.get_events()
    assert isinstance(events, list)

def test_monitor_singleton():
    from backend.task_monitor import get_monitor
    m1 = get_monitor()
    m2 = get_monitor()
    assert m1 is m2