"""索引新鲜度测试：锁住 stale 事实的检测、传播、重建与差异化降级。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.gate.freshness import (
    FreshnessGate,
    apply_capability_policy,
)
from backend.necessity.index.store import Store


def test_changed_file_becomes_stale_and_propagates_to_edges(tmp_path):
    """文件内容变化必须让符号与直接调用边一起标 stale，而非静默读旧图。"""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    db = Store(tmp_path / "index.db")
    try:
        digest = db.put_file_content(str(workspace), "a.py", "def f():\n    return 1\n")
        src = db.upsert_symbol(str(workspace), "caller.py", qualified_name="g",
                               name="g", kind="function", content_hash="caller")
        dst = db.upsert_symbol(str(workspace), "a.py", qualified_name="f",
                               name="f", kind="function", content_hash=digest)
        db.add_edges("caller.py", [(src, dst, "calls", 1)])
        gate = FreshnessGate(db, str(workspace), total_files=5)

        result = gate.check({"a.py": "def f():\n    return 2\n"})

        assert result.stale_files == frozenset({"a.py"})
        assert result.stale_symbols
        assert result.stale_edges
        assert result.callgraph_fresh is False
    finally:
        db.close()


def test_capability_policies_are_not_uniform():
    """A1 保留并标注，A2 拒绝注入，A3 升权，A6 仅降低影响面权重。"""
    stale = FreshnessGate.stale_snapshot({"changed.py"})

    a1 = apply_capability_policy("A1", stale, payload={"impact": ["x"]})
    a2 = apply_capability_policy("A2", stale, payload={"contracts": ["c"]})
    a3 = apply_capability_policy("A3", stale, payload={"permission_tier": "normal"})
    a6 = apply_capability_policy("A6", stale, payload={"impact_weight": 1.0})

    assert a1.payload["impact"] == ["x"]
    assert "影响面可能过期" in a1.note
    assert a2.payload["contracts"] == []
    assert a2.allowed is False
    assert a3.payload["permission_tier"] == "elevated"
    assert a6.payload["impact_weight"] < 1.0


def test_rebuild_failure_keeps_stale_and_alerts(tmp_path):
    """增量重建失败不得把 stale 伪装成 fresh，必须留下可见告警。"""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    db = Store(tmp_path / "index.db")
    try:
        db.put_file_content(str(workspace), "a.py", "old")
        gate = FreshnessGate(db, str(workspace), total_files=1, rebuild_threshold=0.2)

        result = gate.check({"a.py": "new"}, rebuild=lambda files: (_ for _ in ()).throw(RuntimeError("boom")))

        assert result.callgraph_fresh is False
        assert result.alerts
        assert "boom" in result.alerts[0]
        assert result.stale_files == frozenset({"a.py"})
    finally:
        db.close()


def test_rebuild_can_run_async_without_blocking(tmp_path):
    """达到阈值时只提交重建任务，检查调用不应等待异步任务完成。"""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    db = Store(tmp_path / "index.db")
    try:
        db.put_file_content(str(workspace), "a.py", "old")
        gate = FreshnessGate(db, str(workspace), total_files=1, rebuild_threshold=0.2)
        started = []

        result = gate.check({"a.py": "new"}, rebuild=lambda files: started.append(files), asynchronous=True)

        assert result.rebuild_triggered is True
        assert result.callgraph_fresh is False
        result.wait_for_rebuild()
        assert started == [{"a.py"}]
    finally:
        db.close()


def test_fresh_file_is_usable():
    """内容哈希一致时不应制造 stale 信号。"""
    workspace = Path(".").resolve()
    db = Store(":memory:")
    try:
        db.put_file_content(str(workspace), "a.py", "same")
        result = FreshnessGate(db, str(workspace), total_files=1).check({"a.py": "same"})
        assert result.is_stale is False
        assert result.callgraph_fresh is True
    finally:
        db.close()
