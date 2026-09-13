"""Secondary store operations, mixed into ``Store`` to keep ``store.py`` small.

These are grouped by concern — atomic multi-table reindex, file content and the
session read log — and depend only on the connection plumbing (``_write``,
``_read``, ``_rows``) that ``store.py`` provides. A mixin keeps a single public
facade (``Store``) instead of splitting one cohesive data-access object across
a facade plus free functions, and keeps each file under the project's 300-line
budget. The row-shaping helpers are shared so symbol/edge writes have exactly
one canonical parameter order.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping, Sequence

from .store_schema import (
    content_hash as compute_hash,
    normalize_relpath,
    normalize_workspace,
)

EDGE_KINDS = frozenset(("calls", "references", "inherits"))

# Kept in lockstep with the symbols/edges column order in store_schema.SCHEMA_SQL.
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

# OR IGNORE + the COALESCE unique index make edge inserts idempotent: re-running
# a build must not double a symbol's incoming/outgoing call count.
_EDGE_INSERT = """
INSERT OR IGNORE INTO edges (src_id, dst_id, kind, file, line) VALUES (?,?,?,?,?)
"""


def _id_prefix(workspace: str, rel: str) -> str:
    """The ``{workspace}::{rel}::`` prefix shared by every symbol in one file.

    Computed once per file rather than per symbol: building an id inside the
    loop would re-run ``os.path.realpath`` (a syscall chain) for every symbol,
    which is pure waste when indexing a large repo. Callers must pass an
    already-normalised workspace and relpath.
    """
    return f"{workspace}::{rel}::"


def symbol_params(
    workspace: str, rel: str, symbols: Iterable[Mapping[str, Any]], content_hash: str
) -> list[tuple]:
    """Shape symbol mappings into rows; the single canonical insert order.

    ``workspace`` and ``rel`` must already be normalised (the public Store
    methods do this once, before calling in), so ids are assembled by string
    concatenation instead of re-resolving the path per symbol.
    """
    prefix = _id_prefix(workspace, rel)
    rows = []
    for s in symbols:
        qname, name = s.get("qualified_name"), s.get("name")
        if not qname or not name:
            # A symbol without a name has no stable id and cannot be queried;
            # fail before opening a transaction rather than delete-then-crash.
            raise ValueError("symbol requires non-empty qualified_name and name")
        rows.append((
            prefix + str(qname),
            workspace, rel,
            str(qname), str(name),
            str(s.get("kind") or "function"),
            s.get("start_line"), s.get("start_col"),
            s.get("end_line"), s.get("end_col"),
            s.get("signature"), content_hash,
        ))
    return rows


def edge_params(rel: str, edges: Iterable[Mapping[str, Any] | Sequence[Any]]) -> list[tuple]:
    """Shape edges into rows, validating kind and endpoints.

    Accepts either a mapping (``src_id``/``dst_id``/``kind``/``line``) or a tuple
    ``(src, dst, kind[, line])``. Unknown kinds raise here rather than reaching
    SQLite, because ``INSERT OR IGNORE`` still *raises* on a CHECK violation —
    it only ignores uniqueness conflicts — so a silently-swallowed bad kind is
    not possible, and a clear Python error is more useful than an IntegrityError.
    """
    out = []
    for edge in edges:
        if isinstance(edge, Mapping):
            src, dst = edge.get("src_id"), edge.get("dst_id")
            kind, line = edge.get("kind"), edge.get("line")
        else:
            src, dst, kind = edge[0], edge[1], edge[2]
            line = edge[3] if len(edge) > 3 else None
        kind = str(kind or "").lower()
        if kind not in EDGE_KINDS:
            raise ValueError(f"unknown edge kind {kind!r}; expected {sorted(EDGE_KINDS)}")
        if not src or not dst:
            raise ValueError("edge requires src_id and dst_id")
        out.append((src, dst, kind, rel, line))
    return out


def like_prefix_guard(workspace: str, rel: str) -> str:
    """Symbol-id prefix identifying every symbol defined in one file.

    The trailing ``::`` makes it an exact boundary: file ``a.py`` must not
    prefix-match ``a.py.bak``.
    """
    return f"{workspace}::{rel}::"


class StoreOpsMixin:
    """Operations that write more than one table, plus content and read-log."""

    # Provided by the concrete Store; declared for readers/type-checkers.
    _write: Any
    _read: Any

    # --------------------------------------------------- atomic multi-table

    def replace_file_index(
        self,
        workspace: str,
        file: str,
        symbols: Iterable[Mapping[str, Any]],
        edges: Iterable[Mapping[str, Any] | Sequence[Any]],
        *,
        content_hash: str,
    ) -> dict:
        """Reindex a file's symbols **and** outgoing edges in one transaction.

        This is the only correct entry point for a full-file rebuild. Calling
        ``upsert_symbols`` + ``delete_edges_for_file`` + ``add_edges``
        separately leaves a window where a crash yields symbols with no matching
        edges (a half-built graph). One transaction removes that window: either
        the whole file's slice of the graph advances, or none of it does.
        """
        ws, rel = normalize_workspace(workspace), normalize_relpath(file)
        prefix = like_prefix_guard(ws, rel)
        sym_rows = symbol_params(ws, rel, symbols, content_hash)
        edge_rows = edge_params(rel, edges)
        with self._write() as conn:
            conn.execute("DELETE FROM edges WHERE substr(src_id, 1, ?) = ?",
                         (len(prefix), prefix))
            conn.execute("DELETE FROM symbols WHERE workspace=? AND file=?", (ws, rel))
            if sym_rows:
                conn.executemany(_SYMBOL_UPSERT, sym_rows)
            if edge_rows:
                conn.executemany(_EDGE_INSERT, edge_rows)
        return {"symbols": len(sym_rows), "edges": len(edge_rows)}

    # --------------------------------------------------------- file content

    def put_file_content(
        self, workspace: str, path: str, content: str, *,
        size: int | None = None, mtime: float | None = None,
        token_count: int | None = None, indexed_at: float | None = None,
    ) -> str:
        """Store file text + its hash, and nothing about its symbols.

        Deliberately has no ``symbols_json`` parameter: symbol storage belongs
        to the ``symbols`` table alone (INTEGRATION.md §6.1). Returns the hash so
        callers key their symbol queries on it.
        """
        ws, rel = normalize_workspace(workspace), normalize_relpath(path)
        digest = compute_hash(content)
        size = len(content.encode("utf-8")) if size is None else size
        with self._write() as conn:
            conn.execute(
                "INSERT INTO file_content "
                "(workspace, path, content, content_hash, size, mtime, token_count, indexed_at) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace, path) DO UPDATE SET "
                "content=excluded.content, content_hash=excluded.content_hash, "
                "size=excluded.size, mtime=excluded.mtime, "
                "token_count=excluded.token_count, indexed_at=excluded.indexed_at",
                (ws, rel, content, digest, size, mtime, token_count,
                 time.time() if indexed_at is None else indexed_at),
            )
        return digest

    def get_file_content(self, workspace: str, path: str) -> dict | None:
        ws, rel = normalize_workspace(workspace), normalize_relpath(path)
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM file_content WHERE workspace=? AND path=?", (ws, rel)
            ).fetchone()
            return dict(row) if row else None

    # -------------------------------------------------------------- read log

    def log_file_read(
        self, session_id: str, workspace: str, path: str, *,
        validity: str | None = None, at: float | None = None,
    ) -> None:
        """Record a read of a file in a session, incrementing ``read_count``.

        Upsert semantics matter: the table answers "how often did this session
        read this file", so a second read must bump the counter, not add a row.
        ``validity=None`` preserves the existing state — a plain read should not
        downgrade a ``fresh`` entry to ``unknown``.
        """
        if not session_id:
            raise ValueError("session_id must be non-empty")
        ws, rel = normalize_workspace(workspace), normalize_relpath(path)
        ts = time.time() if at is None else at
        with self._write() as conn:
            if validity is None:
                conn.execute(
                    "INSERT INTO file_read_log "
                    "(session_id, workspace, path, read_count, validity, last_read_at) "
                    "VALUES (?,?,?,1,'unknown',?) "
                    "ON CONFLICT(session_id, workspace, path) DO UPDATE SET "
                    "read_count = file_read_log.read_count + 1, "
                    "last_read_at = excluded.last_read_at",
                    (session_id, ws, rel, ts),
                )
            else:
                conn.execute(
                    "INSERT INTO file_read_log "
                    "(session_id, workspace, path, read_count, validity, last_read_at) "
                    "VALUES (?,?,?,1,?,?) "
                    "ON CONFLICT(session_id, workspace, path) DO UPDATE SET "
                    "read_count = file_read_log.read_count + 1, "
                    "validity = excluded.validity, "
                    "last_read_at = excluded.last_read_at",
                    (session_id, ws, rel, validity, ts),
                )

    def get_file_read_log(
        self, session_id: str, workspace: str, path: str
    ) -> dict | None:
        ws, rel = normalize_workspace(workspace), normalize_relpath(path)
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM file_read_log WHERE session_id=? AND workspace=? AND path=?",
                (session_id, ws, rel),
            ).fetchone()
            return dict(row) if row else None

    # ----------------------------------------------------------------- stats

    def stats(self) -> dict[str, Any]:
        """Counts for the CLI ``stats`` command (and health checks)."""
        def scalar(conn: Any, sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])
        with self._read() as conn:
            return {
                "symbols": scalar(conn, "SELECT COUNT(*) FROM symbols"),
                "edges": scalar(conn, "SELECT COUNT(*) FROM edges"),
                "files": scalar(conn, "SELECT COUNT(*) FROM file_content"),
                "read_log": scalar(conn, "SELECT COUNT(*) FROM file_read_log"),
                "edge_kinds": {
                    row["kind"]: row["n"] for row in conn.execute(
                        "SELECT kind, COUNT(*) AS n FROM edges GROUP BY kind"
                    )
                },
            }
