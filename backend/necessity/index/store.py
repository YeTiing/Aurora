"""SQLite store for the structural index: symbols, edges, file content, read log.

Design invariants (see ``INTEGRATION.md`` §6, ``SYSTEM.md`` §"三条铁律"):

1. ``symbols`` is the **single source of truth** for symbol storage. This module
   is the only writer; every other module references symbols by
   ``(workspace, file, content_hash)`` and must not keep its own copy. That rule
   is enforced structurally: ``file_content`` has no symbol-shaped column at
   all (schema lives in ``store_schema``), and the only method that writes
   symbols together with edges is ``replace_file_index``, in one transaction.
2. ``content_hash`` is the validity mechanism. ``query_symbols`` requires it, so
   a caller cannot accidentally read symbols for a file version that no longer
   exists — the query simply returns nothing. There is deliberately no hash-free
   "get symbols for file" method.

Concurrency model (why it is safe, not just ``check_same_thread=False``):

* One connection per ``Store`` instance, plus a ``threading.RLock`` held across
  **every** statement/transaction. The lock is what makes it safe: the Python
  ``sqlite3.Connection`` object is not itself safe for concurrent use, so two
  threads sharing it without a lock can interleave at the C layer.
  ``check_same_thread=False`` merely permits the connection to be *used* off
  its creating thread; it grants no atomicity.
* WAL mode gives readers/writers on the *same* db file concurrency across
  separate connections and processes; ``busy_timeout`` makes a blocked writer
  wait rather than raise ``database is locked``.
* A single lock serialises in-process access. That is a deliberate trade:
  correctness and single-transaction atomicity over read parallelism. If read
  throughput ever matters, the upgrade is a thread-local connection pool, which
  does not change any public method signature.

The secondary surface (atomic reindex, file content, read log, stats) lives in
``store_ops.StoreOpsMixin`` so each file stays within the 300-line budget.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .store_ops import EDGE_KINDS, StoreOpsMixin, edge_params, symbol_params
from .store_trace import StoreTraceMixin
from .store_schema import (
    PRAGMAS,
    SCHEMA_SQL,
    normalize_relpath,
    normalize_workspace,
    symbol_id as build_symbol_id,
)

_SYMBOL_UPSERT = """
INSERT INTO symbols
  (id, workspace, file, qualified_name, name, kind,
   start_line, start_col, end_line, end_col, signature, content_hash)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(id) DO UPDATE SET
  workspace=excluded.workspace, file=excluded.file,
  qualified_name=excluded.qualified_name, name=excluded.name,
  kind=excluded.kind, start_line=excluded.start_line,
  start_col=excluded.start_col, end_line=excluded.end_line,
  end_col=excluded.end_col, signature=excluded.signature,
  content_hash=excluded.content_hash
"""


class Store(StoreOpsMixin, StoreTraceMixin):
    """Owns a SQLite connection to one index db and exposes the graph API."""

    def __init__(self, db_path: str | "os.PathLike[str]", *, timeout: float = 5.0):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(
            self.db_path,
            timeout=timeout,
            check_same_thread=False,  # safe *only* because every use holds _lock
            isolation_level="",       # implicit transactions; explicit commit/rollback
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            for pragma in PRAGMAS:
                self._conn.execute(pragma)
            self._conn.executescript(SCHEMA_SQL)  # idempotent DDL
            self._conn.commit()

    # ------------------------------------------------------------------ plumbing

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Store is closed")

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Transaction scope. On ANY exception the whole scope rolls back, so a
        crash mid-rebuild cannot leave symbols without their edges."""
        with self._lock:
            self._ensure_open()
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_open()
            yield self._conn

    @staticmethod
    def _rows(cur: sqlite3.Cursor) -> list[dict]:
        return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------ symbols

    def upsert_symbol(
        self,
        workspace: str,
        file: str,
        *,
        qualified_name: str,
        name: str,
        kind: str,
        content_hash: str,
        start_line: int | None = None,
        start_col: int | None = None,
        end_line: int | None = None,
        end_col: int | None = None,
        signature: str | None = None,
    ) -> str:
        """Insert or update one symbol in place; returns its stable id.

        ``content_hash`` is mandatory: a symbol row is meaningless without the
        file version it was extracted from, since that is the only thing that
        later proves the row is still valid.
        """
        ws, rel = normalize_workspace(workspace), normalize_relpath(file)
        sid = build_symbol_id(ws, rel, qualified_name)
        rows = symbol_params(ws, rel, [dict(
            qualified_name=qualified_name, name=name, kind=kind,
            start_line=start_line, start_col=start_col, end_line=end_line,
            end_col=end_col, signature=signature)], content_hash)
        with self._write() as conn:
            conn.execute(_SYMBOL_UPSERT, rows[0])
        return sid

    def upsert_symbols(
        self,
        workspace: str,
        file: str,
        symbols: Iterable[Mapping[str, Any]],
        *,
        content_hash: str,
        replace_file: bool = True,
    ) -> int:
        """Bulk-write the symbols of one file in a single transaction.

        With ``replace_file=True`` (default) the file's existing symbols are
        deleted first. That is what a reindex means: symbols are a *function of
        the file version*, so the new set replaces the old one wholesale rather
        than merging, which would leak symbols removed by an edit. Atomic, so a
        failure leaves the previous file index intact instead of a half-set.

        Set ``replace_file=False`` to merge (used when a builder emits symbols
        incrementally); edges for the file are untouched either way — use
        ``replace_file_index`` when symbols and edges must move together.
        """
        ws, rel = normalize_workspace(workspace), normalize_relpath(file)
        rows = symbol_params(ws, rel, symbols, content_hash)
        with self._write() as conn:
            if replace_file:
                conn.execute("DELETE FROM symbols WHERE workspace=? AND file=?", (ws, rel))
            if rows:
                conn.executemany(_SYMBOL_UPSERT, rows)
        return len(rows)

    def query_symbols(self, workspace: str, file: str, *, content_hash: str) -> list[dict]:
        """Symbols for a file **at a specific content hash** — the invalidation path.

        The hash is keyword-only and required, so there is no way to ask for
        "the symbols of this file" without asserting which version you believe
        the file is. Changed file → new hash → no rows → the caller learns the
        index is stale instead of silently receiving old symbols.
        """
        ws, rel = normalize_workspace(workspace), normalize_relpath(file)
        with self._read() as conn:
            cur = conn.execute(
                "SELECT * FROM symbols WHERE workspace=? AND file=? AND content_hash=? "
                "ORDER BY start_line, start_col, qualified_name",
                (ws, rel, content_hash),
            )
            return self._rows(cur)

    def get_symbol(self, sid: str) -> dict | None:
        """Fetch one symbol by id (used by impact analysis to resolve a ref)."""
        with self._read() as conn:
            row = conn.execute("SELECT * FROM symbols WHERE id=?", (sid,)).fetchone()
            return dict(row) if row else None

    # -------------------------------------------------------------------- edges

    def add_edges(
        self, file: str, edges: Iterable[Mapping[str, Any] | Sequence[Any]]
    ) -> int:
        """Insert edges, ignoring ones that already exist. Returns rows added.

        Edge endpoints are symbol ids, which already embed the workspace, so no
        workspace argument is needed. ``file`` is the *referencing* file
        (relative, normalised) and is what ``delete_edges_for_file`` scopes on.
        """
        rel = normalize_relpath(file)
        rows = edge_params(rel, edges)
        if not rows:
            return 0
        with self._write() as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO edges (src_id,dst_id,kind,file,line) VALUES (?,?,?,?,?)",
                rows,
            )
            return conn.total_changes - before

    def delete_edges_for_file(self, workspace: str, file: str) -> int:
        """Drop the outgoing edges of one file (Phase 4 incremental rebuild).

        Scoped by the symbol-id prefix ``{workspace}::{file}::`` rather than by
        the bare ``file`` column, because the same relative path exists in other
        workspaces. Edges whose source file is gone are exactly the ones that
        would otherwise point at deleted symbols.
        """
        ws, rel = normalize_workspace(workspace), normalize_relpath(file)
        prefix = f"{ws}::{rel}::"
        with self._write() as conn:
            before = conn.total_changes
            conn.execute(
                "DELETE FROM edges WHERE substr(src_id, 1, ?) = ?", (len(prefix), prefix)
            )
            return conn.total_changes - before

    def callers(self, dst_id: str, *, kind: str | None = None) -> list[dict]:
        """Who points at ``dst_id``. LEFT JOIN keeps callers whose own symbol row
        is missing (external/stdlib targets) visible instead of dropping them."""
        sql = ("SELECT e.src_id, e.kind, e.file, e.line, "
               "       s.qualified_name AS src_name, s.name AS src_short_name, "
               "       s.file AS src_file, s.kind AS src_kind "
               "FROM edges e LEFT JOIN symbols s ON s.id = e.src_id "
               "WHERE e.dst_id = ?")
        args: list[Any] = [dst_id]
        if kind is not None:
            sql += " AND e.kind = ?"
            args.append(kind)
        with self._read() as conn:
            return self._rows(conn.execute(sql + " ORDER BY e.file, e.line", args))

    def callees(self, src_id: str, *, kind: str | None = None) -> list[dict]:
        """What ``src_id`` points at (symmetric to ``callers``)."""
        sql = ("SELECT e.dst_id, e.kind, e.file, e.line, "
               "       s.qualified_name AS dst_name, s.name AS dst_short_name, "
               "       s.file AS dst_file, s.kind AS dst_kind "
               "FROM edges e LEFT JOIN symbols s ON s.id = e.dst_id "
               "WHERE e.src_id = ?")
        args: list[Any] = [src_id]
        if kind is not None:
            sql += " AND e.kind = ?"
            args.append(kind)
        with self._read() as conn:
            return self._rows(conn.execute(sql + " ORDER BY e.file, e.line", args))

    def edge_count(self) -> int:
        """Total edges — exposed for duplicate-edge assertions in tests/CLI."""
        with self._read() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])


__all__ = ["Store", "EDGE_KINDS"]
