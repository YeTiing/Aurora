"""Context Paging 的降级与装配契约测试。

降级矩阵（INTEGRATION.md §8.2）要求：任何组件失效都不得让 Agent 无法工作。
这里断言工厂在各种坏配置下仍返回可用对象，且 read_file 恒 None。
"""
from __future__ import annotations

import pytest

from backend.necessity.context import ContextPaging, build_context_hooks
from backend.necessity.context.state import FileState, to_relpath

from tests._necessity_context_helpers import (
    BIG,
    BIG_CONTENT,
    cp,      # noqa: F401  (fixture re-export)
    store,   # noqa: F401  (fixture re-export)
    write_workspace_file,
)


# ── 5. 降级 ─────────────────────────────────────────────────────────
def test_factory_without_store_is_usable(tmp_path):
    """无存储 → 工厂仍返回可用对象，read_file 恒 None。"""
    hook = build_context_hooks({"workspace": str(tmp_path)})
    assert hook.read_file("a.py", {}) is None
    assert hook.recall("a.py", {"symbol": "x"}) is None
    assert hook.after_compaction("S") == "S"
    hook.before_compaction([])          # 不抛
    hook.after_write("a.py", "agent")   # 不抛


def test_factory_survives_unwritable_db_path(tmp_path):
    """SQLite 不可写 → 降级为现状，而不是抛异常（INTEGRATION.md §8.2）。"""
    hook = build_context_hooks({"workspace": str(tmp_path),
                                "db_path": str(tmp_path / "no" / "such" / "d" / "x.db")})
    assert hook.read_file("a.py", {}) is None


def test_after_compaction_identity_when_disabled(tmp_path):
    """配置关闭（无 store）时 after_compaction 必须是恒等函数。"""
    hook = build_context_hooks({"workspace": str(tmp_path), "enabled": False})
    assert hook.after_compaction("original") == "original"


# ── 6. 契约与路径 ────────────────────────────────────────────────────

def test_factory_name_is_hard_contract(tmp_path):
    import backend.necessity.context as mod
    assert callable(mod.build_context_hooks)
    assert mod.build_context_hooks({"workspace": str(tmp_path)}) is not None


def test_composes_under_composite_hooks(tmp_path, store):
    """挂到 CompositeHooks 上时 read_file 正常透传（None 也不阻断其他能力）。"""
    from backend.necessity.capability import CompositeHooks
    ws = str(tmp_path / "ws")
    mtime = write_workspace_file(ws, BIG, BIG_CONTENT)
    hook = ContextPaging({"workspace": ws, "store": store, "min_lines_for_index": 5})
    hook.table.note_read(BIG, BIG_CONTENT, mtime=mtime)
    comp = CompositeHooks({"context": hook})
    assert comp.read_file("missing.py", {}) is None
    r = comp.read_file(BIG, {})
    assert r is not None and r.mode == "index"


def test_absolute_path_is_relativised(tmp_path):
    ws = str(tmp_path / "ws")
    rel = to_relpath(str(tmp_path / "ws" / "sub" / "a.py"), ws)
    assert rel == "sub/a.py"
    with pytest.raises(ValueError):
        to_relpath("../outside.py", ws)


def test_shared_table_across_sessions_isolated(tmp_path, store):
    """内容按工作区共享，读取记录按会话隔离（§6.1 两张表的分工）。"""
    ws = str(tmp_path / "ws")
    a = ContextPaging({"workspace": ws, "session_id": "A", "store": store})
    b = ContextPaging({"workspace": ws, "session_id": "B", "store": store})
    a.table.note_read(BIG, BIG_CONTENT, mtime=1.0)
    assert a.table.state(BIG) is FileState.FRESH
    assert b.table.state(BIG) is FileState.UNKNOWN, "会话状态不得互相污染"
    # 但内容（客观事实）是共享的
    assert store.get_file_content(ws, BIG) is not None


def store_row(store, workspace, path):
    row = store.get_file_content(workspace, path)
    return None if row is None else (row["content"], row["content_hash"])
