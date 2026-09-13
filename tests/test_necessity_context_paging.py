"""Context Paging 的行为测试。

重点验证三件最容易做错、做错后果最严重的事：

1. 失效状态机（fresh/stale/dirty/unknown）的每一条迁移。做松 → 服务陈旧
   内容；做紧 → 毫无收益。两个方向都是真实失败。
2. `read_file` 返回 None 表示「本层不管」—— 只有状态表命中才返回结果。
3. 状态表**不受压缩影响**（这是整个能力的核心主张）。

全部离线：临时目录 + 真实 SQLite，无网络、无 LSP。
"""
from __future__ import annotations

import os
import time

import pytest

from backend.necessity.context import ContextPaging, build_context_hooks
from backend.necessity.context.state import FileState, to_relpath
from backend.necessity.index.store import Store

from tests._necessity_context_helpers import (
    BIG,
    BIG_CONTENT,
    content_hash as _hash,
    cp,          # noqa: F401  (fixture re-export)
    register_symbols as _register_symbols,
    store,       # noqa: F401  (fixture re-export)
    store_row,
    write_workspace_file,
)


# ── 1. 状态机：每一条迁移 ────────────────────────────────────────────

def test_unknown_before_any_read(cp):
    assert cp.table.state("src/never_seen.py") is FileState.UNKNOWN


def test_fresh_after_read(cp):
    assert cp.table.state(BIG) is FileState.FRESH


def test_fresh_to_dirty_on_agent_write(cp):
    """writer='agent' → dirty：自己改的，别为了「确认」重读。"""
    cp.after_write(BIG, "agent")
    assert cp.table.state(BIG) is FileState.DIRTY


def test_fresh_to_stale_on_external_write(cp):
    """writer='other' → stale：外部改动，缓存不可信。"""
    cp.after_write(BIG, "other")
    assert cp.table.state(BIG) is FileState.STALE


def test_stale_to_fresh_after_reread(cp):
    """重读（合法）把 stale 归位 fresh —— 状态机的归位动作。"""
    cp.after_write(BIG, "other")
    assert cp.table.state(BIG) is FileState.STALE
    cp.table.note_read(BIG, BIG_CONTENT, mtime=200.0)
    assert cp.table.state(BIG) is FileState.FRESH


def test_dirty_to_fresh_after_verify_read(cp):
    """Agent 写后的校验读：dirty → fresh。"""
    cp.after_write(BIG, "agent")
    cp.table.note_read(BIG, BIG_CONTENT + "\n# touched", mtime=300.0)
    assert cp.table.state(BIG) is FileState.FRESH


def test_stale_to_unknown_when_file_disappears(cp):
    """读失败 → unknown（比 stale 更诚实：连对应版本都不确定了）。"""
    cp.after_write(BIG, "other")
    cp.table.mark_missing(BIG)
    assert cp.table.state(BIG) is FileState.UNKNOWN


def test_external_write_overrides_dirty(cp):
    """外部改动优先级最高：dirty 之后外部又改了 → stale，不能留在 dirty。"""
    cp.after_write(BIG, "agent")
    cp.after_write(BIG, "git")
    assert cp.table.state(BIG) is FileState.STALE


def test_write_transitions_do_not_count_as_reads(cp):
    """写不是读 —— 否则会污染 R / R_waste 的分子分母。"""
    before = cp.table.entry(BIG)["read_count"]
    cp.after_write(BIG, "agent")
    cp.after_write(BIG, "other")
    assert cp.table.entry(BIG)["read_count"] == before


# ── 2. read_file：None = 本层不管 ────────────────────────────────────

def test_read_file_none_when_no_entry(cp):
    """无状态表记录 → None，宿主按原逻辑真读。"""
    assert cp.read_file("src/never_seen.py", {}) is None


def test_read_file_returns_usable_index_on_fresh_hit(cp):
    """命中 fresh → 返回索引，且内容是真实可用的（不是空串）。"""
    r = cp.read_file(BIG, {})
    assert r is not None
    assert r.mode == "index"
    assert r.content and len(r.content) > 20, "索引不能是空串"
    assert "parse" in r.content and "helper_0" in r.content
    assert r.content_hash == _hash(BIG_CONTENT)
    assert {s["name"] for s in r.symbols} == {"parse", "helper_0"}
    assert "仅索引" in r.content or "[仅索引" in r.content


def test_read_file_mode_full_returns_full_text(cp):
    """逃生口：mode='full' 等价现状。"""
    r = cp.read_file(BIG, {"mode": "full"})
    assert r is not None and r.mode == "full"
    assert "helper_59" in r.content, "全文必须包含文件末尾内容"


def test_read_file_returns_none_when_stat_mismatches(cp):
    """(size, mtime) 不一致 → 让宿主真读并覆盖缓存（快速排除路径）。"""
    assert cp.read_file(BIG, {"mtime": 999.0}) is None
    assert cp.table.state(BIG) is FileState.UNKNOWN


def test_read_file_serves_index_when_stat_matches(cp):
    """宿主给了正确的 (size, mtime) → 走快速路径，不 stat 即可返回索引。"""
    st = os.stat(os.path.join(str(cp.workspace), BIG))
    r = cp.read_file(BIG, {"mtime": st.st_mtime, "size": st.st_size})
    assert r is not None and r.mode == "index"


def test_read_file_detects_external_disk_change(cp):
    """§6.4 权威判定：文件在磁盘上被外部改了，即使状态表还是 fresh，
    也不得再服务旧缓存（这是「服务陈旧内容」这一正确性事故的防线）。"""
    assert cp.read_file(BIG, {}) is not None
    time.sleep(0.01)
    write_workspace_file(str(cp.workspace), BIG, BIG_CONTENT + "\n# external\n")
    assert cp.read_file(BIG, {}) is None, "磁盘 mtime 变了必须退回宿主真读"
    assert cp.table.state(BIG) is FileState.UNKNOWN


def test_host_reread_closes_the_loop(cp):
    """宿主真读后回报 → 状态表归位 fresh，下一次读又走索引。"""
    cp.after_write(BIG, "other")
    assert cp.read_file(BIG, {}) is None
    fresh_content = BIG_CONTENT + "\n# host reread\n"
    mtime = write_workspace_file(str(cp.workspace), BIG, fresh_content)
    cp.note_read(BIG, fresh_content, mtime=mtime)     # 宿主回报
    _register_symbols(cp.store, str(cp.workspace), BIG, fresh_content)
    r = cp.read_file(BIG, {})
    assert r is not None and r.content_hash == _hash(fresh_content)


def test_read_file_none_after_agent_write(cp):
    """核心断言：after_write(path,'agent') 后不得服务旧缓存内容。"""
    before = cp.read_file(BIG, {})
    assert before is not None
    cp.after_write(BIG, "agent")
    assert cp.read_file(BIG, {}) is None, "不能把 Agent 自己改之前的缓存当新内容返回"


def test_read_file_none_after_external_write(cp):
    """核心断言：after_write(path,'other') 后缓存视为 stale，不再服务。"""
    cp.after_write(BIG, "other")
    assert cp.read_file(BIG, {}) is None


def test_read_file_recovers_only_after_a_real_reread(cp):
    """合法重读后恢复服务，且提供的是新内容 —— 证明归位链路完整。"""
    cp.after_write(BIG, "other")
    assert cp.read_file(BIG, {}) is None
    new_content = BIG_CONTENT + "\n# new external line"
    mtime = write_workspace_file(str(cp.workspace), BIG, new_content)
    cp.table.note_read(BIG, new_content, mtime=mtime)
    _register_symbols(cp.store, str(cp.workspace), BIG, new_content)
    r = cp.read_file(BIG, {})
    assert r is not None
    assert r.content_hash == _hash(new_content) != _hash(BIG_CONTENT)


def test_read_file_force_serves_dirty(cp):
    """force=True 是显式逃生口：已知会读到可能过期的缓存也照样返回。"""
    cp.after_write(BIG, "agent")
    r = cp.read_file(BIG, {"force": True})
    assert r is not None and r.content_hash == _hash(BIG_CONTENT)


# ── 3. recall：按符号取回真实内容 ────────────────────────────────────

def test_recall_symbol_returns_actual_source(cp):
    r = cp.recall(BIG, {"symbol": "parse"})
    assert r is not None and r.mode == "recall"
    assert "def parse(x):" in r.content
    assert "return x + 1" in r.content
    assert "12|" in r.content or "1|" in r.content, "应带行号前缀"


def test_recall_lines_returns_range(cp):
    r = cp.recall(BIG, {"lines": [1, 2]})
    assert r is not None
    assert "def parse(x):" in r.content


def test_recall_grep_finds_match(cp):
    r = cp.recall(BIG, {"grep": "helper_7"})
    assert r is not None and "helper_7" in r.content


def test_recall_returns_none_without_cache(cp):
    assert cp.recall("src/never_seen.py", {"symbol": "x"}) is None


# ── 4. 压缩契约：状态表必须存活 ──────────────────────────────────────

def test_compaction_injects_file_state_index(cp, store):
    """不变量 2：压缩后摘要里必须有 file_state 索引。"""
    cp.before_compaction([])
    out = cp.after_compaction("SUMMARY")
    assert out.startswith("SUMMARY")
    assert "## 已读文件" in out
    assert "big.py" in out
    assert "@" + _hash(BIG_CONTENT) in out
    assert "parse" in out


def test_state_table_survives_compaction(cp):
    """核心主张：状态表不受压缩影响。"""
    entries_before = cp.table.entries()
    content_before = store_row(cp.store, str(cp.workspace), BIG)

    cp.before_compaction(["m1", "m2", "m3"])
    out = cp.after_compaction("SUMMARY")

    assert cp.table.entries() == entries_before, "压缩不得改变状态表条目"
    assert store_row(cp.store, str(cp.workspace), BIG) == content_before, (
        "压缩不得读写 file_content（不变量 1）"
    )
    # 压缩后依然命中 fresh → Agent 无需重读
    r = cp.read_file(BIG, {})
    assert r is not None and r.mode == "index"
    assert "big.py" in out


def test_compaction_round_trip_is_deterministic(cp):
    """同一状态表 → 两次压缩注入逐字一致（可验证的「存活」）。"""
    cp.before_compaction([])
    first = cp.after_compaction("S")
    cp.before_compaction(["different", "messages"])
    second = cp.after_compaction("S" * 100)
    assert first.split("\n\n", 1)[1] == second.split("\n\n", 1)[1]


def test_compaction_excludes_stale_entries(cp):
    """stale 内容不可信，不得出现在注入索引里。"""
    cp.after_write(BIG, "other")
    cp.before_compaction([])
    out = cp.after_compaction("S")
    assert "big.py" not in out


def test_compaction_truncates_with_note(cp):
    """超 max_index_entries 必须显式注明，不得静默丢弃（§6.5 边界）。"""
    for i in range(5):
        cp.table.note_read(f"src/f{i}.py", f"# file {i}\n" * 60, mtime=float(i))
    cp.max_entries = 2
    cp.before_compaction([])
    out = cp.after_compaction("S")
    assert "还有" in out and "个文件未列出" in out


def test_before_compaction_does_not_touch_file_content(cp):
    """不变量 1：压缩快照只读 file_read_log，不碰 file_content。"""
    row = store_row(cp.store, str(cp.workspace), BIG)
    cp.before_compaction([])
    assert store_row(cp.store, str(cp.workspace), BIG) == row
