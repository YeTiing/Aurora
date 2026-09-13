"""Behavioural tests for ``core.index.store``.

These exercise the invariants the design docs make load-bearing: content-hash
invalidation, workspace isolation, duplicate-edge idempotence, cross-table
atomicity, read-log upsert, and thread safety. Everything runs offline against
``tmp_path``; no fixtures beyond pytest's own builtins are used.
"""

from __future__ import annotations

import threading

import pytest

from backend.necessity.index.store import Store
from backend.necessity.index.store_schema import (
    content_hash,
    normalize_relpath,
    normalize_workspace,
    symbol_id,
)


# --------------------------------------------------------------------- fixtures

@pytest.fixture()
def store(tmp_path):
    st = Store(str(tmp_path / "index.db"))
    yield st
    st.close()


def _sym(store, ws, file, qname, *, hash_, kind="function", line=1, name=None):
    return store.upsert_symbol(
        ws, file, qualified_name=qname, name=name or qname.split(".")[-1],
        kind=kind, content_hash=hash_, start_line=line, end_line=line + 3,
    )


def _edge(src, dst, kind="calls", line=10):
    return {"src_id": src, "dst_id": dst, "kind": kind, "line": line}


def _sid(ws, file, qname):
    """Build the exact id the store would use (workspace-prefixed)."""
    return symbol_id(ws, file, qname)


class _FlakyConn:
    """Proxy that delegates to a real connection but raises on chosen calls.

    ``sqlite3.Connection`` methods are read-only C attributes, so they cannot be
    monkeypatched on the instance; swapping the whole connection object is the
    way to inject a mid-transaction failure without touching production code.
    """

    def __init__(self, conn, fail_when):
        self._conn = conn
        self._fail_when = fail_when

    def executemany(self, sql, params):
        if self._fail_when(sql, params):
            raise RuntimeError("simulated crash mid-write")
        return self._conn.executemany(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ------------------------------------------------------------- happy-path CRUD

def test_symbol_upsert_and_query_roundtrip(store, tmp_path):
    ws = str(tmp_path / "proj")
    h = content_hash("def foo(): pass")
    sid = _sym(store, ws, "src/a.py", "foo", hash_=h, line=7)

    assert sid == symbol_id(ws, "src/a.py", "foo")
    assert sid == f"{normalize_workspace(ws)}::src/a.py::foo"
    rows = store.query_symbols(ws, "src/a.py", content_hash=h)
    assert len(rows) == 1
    assert rows[0]["qualified_name"] == "foo"
    assert rows[0]["name"] == "foo"
    assert rows[0]["start_line"] == 7
    assert rows[0]["content_hash"] == h
    assert store.get_symbol(sid)["kind"] == "function"


def test_symbol_upsert_updates_in_place_not_duplicates(store, tmp_path):
    ws = str(tmp_path / "proj")
    h = content_hash("v1")
    _sym(store, ws, "a.py", "foo", hash_=h, line=1)
    _sym(store, ws, "a.py", "foo", hash_=h, line=99, kind="method")

    rows = store.query_symbols(ws, "a.py", content_hash=h)
    assert len(rows) == 1
    assert rows[0]["start_line"] == 99
    assert rows[0]["kind"] == "method"


def test_bulk_upsert_replaces_file_symbol_set(store, tmp_path):
    ws = str(tmp_path / "proj")
    h1 = content_hash("v1")
    store.upsert_symbols(ws, "a.py", [
        {"qualified_name": "A", "name": "A", "kind": "class"},
        {"qualified_name": "A.m", "name": "m", "kind": "method"},
    ], content_hash=h1)
    h2 = content_hash("v2")
    store.upsert_symbols(ws, "a.py", [
        {"qualified_name": "B", "name": "B", "kind": "class"},
    ], content_hash=h2)

    assert store.query_symbols(ws, "a.py", content_hash=h1) == []  # old version gone
    rows = store.query_symbols(ws, "a.py", content_hash=h2)
    assert [r["qualified_name"] for r in rows] == ["B"]


def test_edges_roundtrip_callers_and_callees(store, tmp_path):
    ws = str(tmp_path / "proj")
    h = content_hash("code")
    caller = _sym(store, ws, "a.py", "caller", hash_=h)
    callee = _sym(store, ws, "b.py", "callee", hash_=h)
    added = store.add_edges("a.py", [_edge(caller, callee, "calls", 12)])

    assert added == 1
    inbound = store.callers(callee)
    assert len(inbound) == 1
    assert inbound[0]["src_id"] == caller
    assert inbound[0]["file"] == "a.py"
    assert inbound[0]["src_name"] == "caller"
    assert inbound[0]["kind"] == "calls"

    outbound = store.callees(caller)
    assert len(outbound) == 1
    assert outbound[0]["dst_id"] == callee
    assert outbound[0]["dst_name"] == "callee"


def test_edges_kind_filter(store, tmp_path):
    ws = str(tmp_path / "proj")
    h = content_hash("code")
    a = _sym(store, ws, "a.py", "a", hash_=h)
    b = _sym(store, ws, "b.py", "b", hash_=h)
    c = _sym(store, ws, "c.py", "c", hash_=h)
    store.add_edges("a.py", [_edge(a, b, "calls"), _edge(a, c, "references")])

    assert len(store.callees(a)) == 2
    assert [e["dst_id"] for e in store.callees(a, kind="references")] == [c]
    assert store.callers(b, kind="references") == []


# --------------------------------------------------- auto-invalidation (core)

def test_query_with_old_hash_after_file_change_returns_nothing(store, tmp_path):
    """The central mechanism: hash change invalidates symbols, silently and safely."""
    ws = str(tmp_path / "proj")
    old_text = "def foo(): pass"
    h1 = content_hash(old_text)
    _sym(store, ws, "a.py", "foo", hash_=h1)

    assert len(store.query_symbols(ws, "a.py", content_hash=h1)) == 1

    new_text = "def foo(x): return x"       # file edited on disk
    h2 = content_hash(new_text)
    assert h2 != h1

    # Caller asserting the NEW hash must not receive the OLD symbol.
    assert store.query_symbols(ws, "a.py", content_hash=h2) == []
    # The stale-id validity rule: the old row is still on record under h1 only.
    (stale,) = store.query_symbols(ws, "a.py", content_hash=h1)
    assert stale["content_hash"] == h1


def test_file_content_hash_matches_manual_hash(store, tmp_path):
    ws = str(tmp_path / "proj")
    text = "x = 1\n"
    digest = store.put_file_content(ws, "a.py", text, size=len(text), mtime=1.5)
    assert digest == content_hash(text)
    row = store.get_file_content(ws, "a.py")
    assert row["content"] == text
    assert row["content_hash"] == digest
    assert "symbols" not in row and "symbols_json" not in row


def test_content_hash_algorithm_matches_spec():
    """Locked to CONTEXT_PAGING.md §6.1: sha256 truncated to 16 hex chars.

    Other modules compute this hash independently (symbols.file_content_hash);
    if the algorithm drifts, validity checks silently stop matching.
    """
    assert content_hash("") == "e3b0c44298fc1c14"
    assert len(content_hash("anything")) == 16


def test_file_content_put_is_upsert(store, tmp_path):
    ws = str(tmp_path / "proj")
    store.put_file_content(ws, "a.py", "v1")
    store.put_file_content(ws, "a.py", "v2")
    row = store.get_file_content(ws, "a.py")
    assert row["content"] == "v2"
    assert store.stats()["files"] == 1


def test_replace_file_index_couples_hash_and_graph(store, tmp_path):
    ws = str(tmp_path / "proj")
    old_fn, dep = _sid(ws, "a.py", "old_fn"), _sid(ws, "b.py", "dep")
    h1 = content_hash("old")
    store.replace_file_index(
        ws, "a.py",
        [{"qualified_name": "old_fn", "name": "old_fn", "kind": "function"}],
        [_edge(old_fn, dep)], content_hash=h1,
    )
    assert len(store.query_symbols(ws, "a.py", content_hash=h1)) == 1
    assert len(store.callers(dep)) == 1

    h2 = content_hash("new")
    store.replace_file_index(
        ws, "a.py",
        [{"qualified_name": "new_fn", "name": "new_fn", "kind": "function"}],
        [], content_hash=h2,
    )
    assert store.query_symbols(ws, "a.py", content_hash=h1) == []
    assert len(store.query_symbols(ws, "a.py", content_hash=h2)) == 1
    # Old edge removed together with the symbols that justified it.
    assert store.callers(dep) == []
    assert store.edge_count() == 0


def test_delete_edges_for_file_scoped_to_workspace(store, tmp_path):
    ws_a, ws_b = str(tmp_path / "A"), str(tmp_path / "B")
    h = content_hash("x")
    sa = _sym(store, ws_a, "same.py", "f", hash_=h)
    sb = _sym(store, ws_b, "same.py", "f", hash_=h)
    target = _sym(store, ws_a, "other.py", "g", hash_=h, kind="class")
    assert sa != sb  # workspace is part of the id, so isolation holds even here
    store.add_edges("same.py", [_edge(sa, target), _edge(sb, target)])

    removed = store.delete_edges_for_file(ws_a, "same.py")
    assert removed == 1
    remaining = store.callers(target)
    assert [e["src_id"] for e in remaining] == [sb]   # workspace B untouched


# ------------------------------------------------------- duplicate-edge policy

def test_duplicate_edge_inserted_twice_yields_one_row(store, tmp_path):
    ws = str(tmp_path / "proj")
    h = content_hash("code")
    a = _sym(store, ws, "a.py", "a", hash_=h)
    b = _sym(store, ws, "b.py", "b", hash_=h)
    store.add_edges("a.py", [_edge(a, b)])
    store.add_edges("a.py", [_edge(a, b)])

    assert store.edge_count() == 1
    assert len(store.callers(b)) == 1          # call count not inflated


def test_duplicate_edge_with_null_line_still_deduped(store, tmp_path):
    ws = str(tmp_path / "proj")
    h = content_hash("code")
    a = _sym(store, ws, "a.py", "a", hash_=h)
    b = _sym(store, ws, "b.py", "b", hash_=h)
    store.add_edges("a.py", [{"src_id": a, "dst_id": b, "kind": "calls", "line": None}])
    store.add_edges("a.py", [{"src_id": a, "dst_id": b, "kind": "calls", "line": None}])
    assert store.edge_count() == 1


def test_same_pair_different_kind_is_a_distinct_edge(store, tmp_path):
    ws = str(tmp_path / "proj")
    h = content_hash("code")
    a = _sym(store, ws, "a.py", "a", hash_=h)
    b = _sym(store, ws, "b.py", "b", hash_=h)
    store.add_edges("a.py", [_edge(a, b, "calls"), _edge(a, b, "references")])
    assert store.edge_count() == 2


def test_unknown_edge_kind_rejected(store, tmp_path):
    with pytest.raises(ValueError, match="unknown edge kind"):
        store.add_edges("a.py", [_edge("a.py::a", "b.py::b", "implements")])


# ---------------------------------------------------------- workspace isolation

def test_same_relpath_two_workspaces_do_not_collide(store, tmp_path):
    ws_a, ws_b = str(tmp_path / "A"), str(tmp_path / "B")
    h = content_hash("code")
    _sym(store, ws_a, "src/main.py", "run", hash_=h, line=1)
    _sym(store, ws_b, "src/main.py", "run", hash_=h, line=2)

    rows_a = store.query_symbols(ws_a, "src/main.py", content_hash=h)
    rows_b = store.query_symbols(ws_b, "src/main.py", content_hash=h)
    assert len(rows_a) == 1 and len(rows_b) == 1
    assert rows_a[0]["start_line"] == 1
    assert rows_b[0]["start_line"] == 2
    assert rows_a[0]["workspace"] != rows_b[0]["workspace"]
    assert store.stats()["symbols"] == 2


# ------------------------------------------------------- path normalization

@pytest.mark.parametrize("variant", [r"C:\Foo", "c:/foo", "C:/Foo/", r"c:\\foo"])
def test_windows_workspace_variants_map_to_one_key(store, tmp_path, variant):
    ref = normalize_workspace(r"C:\Foo")
    assert normalize_workspace(variant) == ref
    # And a symbol written under one spelling is found under another.
    h = content_hash("code")
    _sym(store, r"C:\Foo", "a.py", "f", hash_=h)
    assert len(store.query_symbols("c:/foo", "a.py", content_hash=h)) == 1


def test_relpath_and_workspace_normalize_separators():
    assert normalize_relpath(r"src\pkg\mod.py") == "src/pkg/mod.py"
    assert normalize_relpath("./src/pkg/mod.py") == "src/pkg/mod.py"
    assert normalize_workspace(r"D:\Proj\Sub\\") == normalize_workspace("d:/proj/sub")


@pytest.mark.parametrize("bad", ["/abs/a.py", r"C:\abs\a.py", "../escape.py", "a/../../b.py"])
def test_absolute_and_escaping_relpaths_rejected(bad):
    with pytest.raises(ValueError):
        normalize_relpath(bad)


# ----------------------------------------------------------------- atomicity

def test_replace_file_index_rolls_back_on_failure(store, tmp_path):
    """A crash between the symbol write and the edge write must leave NO partial state."""
    ws = str(tmp_path / "proj")
    keep, dep = _sid(ws, "a.py", "keep"), _sid(ws, "b.py", "dep")
    h1 = content_hash("v1")
    store.replace_file_index(
        ws, "a.py", [{"qualified_name": "keep", "name": "keep", "kind": "function"}],
        [_edge(keep, dep)], content_hash=h1,
    )
    assert len(store.query_symbols(ws, "a.py", content_hash=h1)) == 1
    assert store.edge_count() == 1

    # Inject a failure *inside* the transaction: the edge executemany blows up
    # after the symbols have already been written and old rows deleted.
    real_conn = store._conn
    store._conn = _FlakyConn(
        real_conn, lambda sql, params: "INTO edges" in sql and bool(params)
    )
    h2 = content_hash("v2")
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            store.replace_file_index(
                ws, "a.py",
                [{"qualified_name": "new", "name": "new", "kind": "function"}],
                [_edge(_sid(ws, "a.py", "new"), _sid(ws, "b.py", "dep2"))],
                content_hash=h2,
            )
    finally:
        store._conn = real_conn

    # Nothing half-applied: old version intact, new version absent, edges intact.
    assert len(store.query_symbols(ws, "a.py", content_hash=h1)) == 1
    assert store.query_symbols(ws, "a.py", content_hash=h2) == []
    assert [e["dst_id"] for e in store.callees(keep)] == [dep]
    assert store.edge_count() == 1
    assert not real_conn.in_transaction


def test_upsert_symbols_rejects_bad_row_before_writing(store, tmp_path):
    """Validation happens before the transaction, so a bad row cannot destroy good data."""
    ws = str(tmp_path / "proj")
    h = content_hash("v1")
    store.upsert_symbols(ws, "a.py", [
        {"qualified_name": "ok", "name": "ok", "kind": "function"},
    ], content_hash=h)

    with pytest.raises(ValueError):
        store.upsert_symbols(ws, "a.py", [
            {"qualified_name": "good", "name": "good", "kind": "function"},
            {"qualified_name": "", "name": "bad", "kind": "function"},
        ], content_hash=content_hash("v2"))

    # Replace-file delete never ran: the previous symbol set is intact.
    assert len(store.query_symbols(ws, "a.py", content_hash=h)) == 1


def test_upsert_symbols_rolls_back_delete_on_write_failure(store, tmp_path):
    """If the symbol insert fails inside the txn, the pre-emptive DELETE rolls back."""
    ws = str(tmp_path / "proj")
    h1 = content_hash("v1")
    store.upsert_symbols(ws, "a.py", [
        {"qualified_name": "original", "name": "original", "kind": "function"},
    ], content_hash=h1)

    real_conn = store._conn
    store._conn = _FlakyConn(
        real_conn, lambda sql, params: "INTO symbols" in sql and bool(params)
    )
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            store.upsert_symbols(ws, "a.py", [
                {"qualified_name": "replacement", "name": "replacement",
                 "kind": "function"},
            ], content_hash=content_hash("v2"))
    finally:
        store._conn = real_conn

    # The DELETE that preceded the failed insert must not have committed.
    rows = store.query_symbols(ws, "a.py", content_hash=h1)
    assert [r["qualified_name"] for r in rows] == ["original"]
    assert store.stats()["symbols"] == 1


# ------------------------------------------------------------------ read log

def test_file_read_log_upsert_increments_read_count(store, tmp_path):
    ws = str(tmp_path / "proj")
    store.log_file_read("sess-1", ws, "a.py", validity="fresh", at=100.0)
    store.log_file_read("sess-1", ws, "a.py", validity="stale", at=200.0)

    row = store.get_file_read_log("sess-1", ws, "a.py")
    assert row["read_count"] == 2
    assert row["validity"] == "stale"
    assert row["last_read_at"] == 200.0
    assert store.stats()["read_log"] == 1


def test_read_log_keeps_validity_when_not_specified(store, tmp_path):
    ws = str(tmp_path / "proj")
    store.log_file_read("s", ws, "a.py", validity="fresh")
    store.log_file_read("s", ws, "a.py")          # plain re-read
    row = store.get_file_read_log("s", ws, "a.py")
    assert row["read_count"] == 2
    assert row["validity"] == "fresh"             # not downgraded


def test_read_log_isolated_per_session(store, tmp_path):
    ws = str(tmp_path / "proj")
    store.log_file_read("s1", ws, "a.py")
    store.log_file_read("s2", ws, "a.py")
    store.log_file_read("s2", ws, "a.py")
    assert store.get_file_read_log("s1", ws, "a.py")["read_count"] == 1
    assert store.get_file_read_log("s2", ws, "a.py")["read_count"] == 2


# --------------------------------------------------------------------- stats

def test_stats_counts(store, tmp_path):
    ws = str(tmp_path / "proj")
    h = content_hash("code")
    a = _sym(store, ws, "a.py", "a", hash_=h)
    b = _sym(store, ws, "b.py", "b", hash_=h, kind="class")
    store.add_edges("a.py", [_edge(a, b, "calls")])
    store.put_file_content(ws, "a.py", "code")

    s = store.stats()
    assert s["symbols"] == 2
    assert s["edges"] == 1
    assert s["files"] == 1
    assert s["edge_kinds"] == {"calls": 1}


# ----------------------------------------------------------- schema / idempotency

def test_schema_creation_is_idempotent(tmp_path):
    path = str(tmp_path / "index.db")
    for _ in range(3):
        st = Store(path)
        st.close()
    st = Store(path)
    st.put_file_content(str(tmp_path), "a.py", "x")
    assert st.stats()["files"] == 1
    st.close()


def test_wal_mode_enabled(tmp_path):
    st = Store(str(tmp_path / "index.db"))
    mode = st._conn.execute("PRAGMA journal_mode").fetchone()[0]
    st.close()
    assert mode.lower() == "wal"


# --------------------------------------------------------------- concurrency

def test_concurrent_writers_do_not_corrupt_or_raise(tmp_path):
    st = Store(str(tmp_path / "index.db"))
    ws = str(tmp_path / "proj")
    h = content_hash("code")
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=5)
            for i in range(25):
                src = f"w{n}.py::f{i}"
                dst = "shared.py::target"
                st.upsert_symbol(ws, f"w{n}.py", qualified_name=f"f{i}", name=f"f{i}",
                                 kind="function", content_hash=h, start_line=i)
                st.add_edges(f"w{n}.py", [{"src_id": src, "dst_id": dst,
                                           "kind": "calls", "line": i}])
                st.log_file_read(f"session-{n}", ws, f"w{n}.py")
                st.callers(dst)
        except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()

    assert errors == []
    # sqlite is locked-free and consistent: one edge per (worker, i) pair.
    assert st.stats()["edges"] == 8 * 25
    assert len(st.callers("shared.py::target")) == 8 * 25
    assert st._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    st.close()


def test_two_store_instances_same_file_coexist(tmp_path):
    """Separate connections to one db file must not deadlock or corrupt (WAL)."""
    path = str(tmp_path / "index.db")
    ws = str(tmp_path / "proj")
    h = content_hash("code")
    a, b = Store(path), Store(path)
    a.upsert_symbol(ws, "a.py", qualified_name="f", name="f", kind="function",
                    content_hash=h)
    b.upsert_symbol(ws, "b.py", qualified_name="g", name="g", kind="function",
                    content_hash=h)
    assert a.stats()["symbols"] == 2
    assert b.stats()["symbols"] == 2
    a.close()
    b.close()


def test_operations_after_close_raise(tmp_path):
    st = Store(str(tmp_path / "index.db"))
    st.close()
    with pytest.raises(RuntimeError, match="closed"):
        st.stats()


# --- cross-process helper (module level so Windows 'spawn' can pickle it) ---

def _process_worker(db_path: str, ws: str, n: int) -> None:
    st = Store(db_path)
    h = content_hash(f"worker-{n}")
    try:
        for i in range(10):
            st.upsert_symbol(ws, f"w{n}.py", qualified_name=f"f{i}", name=f"f{i}",
                             kind="function", content_hash=h, start_line=i)
            st.add_edges(f"w{n}.py", [{"src_id": f"{normalize_workspace(ws)}::w{n}.py::f{i}",
                                       "dst_id": f"{normalize_workspace(ws)}::shared.py::t",
                                       "kind": "calls", "line": i}])
    finally:
        st.close()


def test_concurrent_processes_share_wal_db(tmp_path):
    """Separate processes each hold their own connection; busy_timeout must make
    them queue rather than raise 'database is locked'."""
    from concurrent.futures import ProcessPoolExecutor

    db_path = str(tmp_path / "index.db")
    ws = str(tmp_path / "proj")
    Store(db_path).close()  # create schema/WAL before the workers race for it

    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_process_worker, db_path, ws, n) for n in range(4)]
        for f in futures:
            f.result(timeout=60)

    st = Store(db_path)
    try:
        assert st.stats()["symbols"] == 40
        assert st.stats()["edges"] == 40
        assert st._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        st.close()
