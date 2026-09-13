"""SQLite schema for the structural index — DDL, pragmas and path normalisation.

Kept separate from ``store.py`` so that the query/store logic stays under the
300-line budget while the (long, comment-heavy) DDL lives on its own.

Schema authority: ``INTEGRATION.md`` §6.2 (the corrected schema). Where
``INDEX.md`` §4 differs, INTEGRATION.md wins:

* ``symbols.id``            -- INDEX.md §4 says ``{workspace}::{relpath}::{qname}``
                               while its own §3.2 says ``{relpath}::{qname}``.
                               We keep the workspace in the id, because the
                               PRIMARY KEY is on ``id``: without it,
                               ``same.py::f`` in two workspaces would be one row
                               and multi-workspace isolation would be silently
                               broken. It also lets ``delete_edges_for_file``
                               scope edge deletion by an id prefix, which the
                               bare ``file`` column alone cannot do.
* ``file_content``          -- corrected schema uses ``path``, not ``file``.
* ``file_read_log``         -- Context Paging §7 says validity is
                               ``fresh|stale|unknown``; INTEGRATION.md §6.2
                               adds ``dirty`` (used after the Agent writes).
                               We accept ``fresh|stale|dirty|unknown``.
"""

from __future__ import annotations

import hashlib
import os

SCHEMA_VERSION = 1

# Row kinds that are allowed in ``edges.kind``. Enforced by a CHECK constraint,
# not just by documentation, so a typo from the graph builder fails loudly
# instead of polluting call counts with an unmatchable relationship.
EDGE_KINDS = ("calls", "references", "inherits")

# ``file_read_log.validity`` states. ``dirty`` is the post-write state added by
# INTEGRATION.md §6.2; the other three come from CONTEXT_PAGING.md §7.
VALIDITY_STATES = ("fresh", "stale", "dirty", "unknown")

# Applied at every connection open, before anything else. Why each pragma:
#   * journal_mode=WAL   -- single writer + concurrent readers without global
#     lock contention; a crash never leaves a half-written page in the main db.
#   * synchronous=NORMAL -- with WAL this is crash-safe at the transaction
#     boundary (the guarantee we actually need: no torn multi-table writes).
#     FULL would fsync on every commit and is pointless here.
#   * busy_timeout=5000  -- a writer blocked by another writer waits instead of
#     raising "database is locked" immediately. This, NOT check_same_thread, is
#     what makes cross-thread access behave.
#   * foreign_keys=OFF   -- deliberate. ``edges`` is a graph, not a referential
#     tree: retiring a symbol must not cascade-delete edges underneath an
#     in-flight rebuild. Dangling edges are filtered by JOINing ``symbols``.
#   * wal_autocheckpoint   -- keeps the -wal file from growing without bound in
#     long-lived sessions.
PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=OFF",
    "PRAGMA busy_timeout=5000",
    "PRAGMA wal_autocheckpoint=1000",
)

# Idempotent DDL. Every statement is IF NOT EXISTS so ``init_schema`` can be
# called on every process start (and from every thread) without racing.
#
# EDGES PRIMARY KEY — the docs give edges no key. We add no surrogate id, but we
# DO add a UNIQUE index (below) over the edge's *identity*: the pair of symbols,
# the relation kind, and the reference site. Without it, re-running a build
# re-inserts every edge and silently doubles call counts, which is exactly what
# the callers/impact queries must never report. A surrogate rowid id would not
# help: it would make every duplicate distinct instead of collapsing them.
#
# The index uses COALESCE expressions because SQLite treats NULLs as distinct in
# a UNIQUE index: without it, the same edge with a NULL file/line could be
# inserted repeatedly (edges.file/line are nullable in the doc schema).
#
# ``CHECK(kind IN ...)`` on edges means ``INSERT ... ON CONFLICT DO NOTHING``
# still raises on a bad kind (verified against sqlite 3.45); callers normalise
# unknown kinds instead. ``file_read_log.validity`` deliberately has no CHECK —
# an unknown validity token is a data-state issue that must not block a read
# path (INTEGRATION.md §8.2: a degradation must never stop the Agent working).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS symbols (
  id             TEXT PRIMARY KEY,          -- {workspace}::{relpath}::{qname}
  workspace      TEXT NOT NULL,             -- normalised absolute workspace path
  file           TEXT NOT NULL,             -- workspace-relative, '/' separated
  qualified_name TEXT NOT NULL,
  name           TEXT NOT NULL,
  kind           TEXT NOT NULL,             -- function | method | class
  start_line     INT, start_col INT,
  end_line       INT, end_col INT,
  signature      TEXT,
  content_hash   TEXT NOT NULL,             -- hash of the file at index time
  UNIQUE(workspace, file, qualified_name)
);

-- No surrogate id by design; identity is (src,dst,kind,file,line) and is made
-- enforceable by idx_edges_unique below. Inserts use INSERT OR IGNORE.
CREATE TABLE IF NOT EXISTS edges (
  src_id TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  kind   TEXT NOT NULL CHECK(kind IN ('calls','references','inherits')),
  file   TEXT,                              -- referencing file (relative)
  line   INT
);

CREATE TABLE IF NOT EXISTS file_content (
  workspace    TEXT NOT NULL,
  path         TEXT NOT NULL,               -- relative; only content+hash live
  content      TEXT NOT NULL,               -- here, NEVER symbols (see §6.1)
  content_hash TEXT NOT NULL,
  size         INTEGER,
  mtime        REAL,
  token_count  INTEGER,
  indexed_at   REAL,
  PRIMARY KEY (workspace, path)
);

CREATE TABLE IF NOT EXISTS file_read_log (
  session_id   TEXT NOT NULL,
  workspace    TEXT NOT NULL,
  path         TEXT NOT NULL,
  read_count   INTEGER DEFAULT 0,
  validity     TEXT NOT NULL,               -- fresh | stale | dirty | unknown
  last_read_at REAL,
  PRIMARY KEY (session_id, workspace, path)
);

-- 轨迹事件表（INTEGRATION.md §5.3 要求加在此处）。
-- 这张表曾被遗漏：core/index/trace.py 依赖 append/query_agent_events，
-- 但 schema 里从没有 agent_event —— 导致 Attribution 与 Gate 0 在真实数据上
-- 永远拿不到事件（只能返回 unknown），且**不报错**。
-- 只存事实：payload 是 JSON 文本，不做字段拆解（判断留给 signals.py）。
CREATE TABLE IF NOT EXISTS agent_event (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  kind       TEXT NOT NULL,               -- file_read | file_write | compaction |
                                          -- edit_revert | constraint_violation | test_run
  turn       INTEGER DEFAULT 0,
  ts         REAL NOT NULL,
  payload    TEXT NOT NULL                -- JSON；只放客观数据，不放判断结论
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(workspace, file, content_hash);
CREATE INDEX IF NOT EXISTS idx_agent_event_session ON agent_event(session_id, kind);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique
  ON edges(src_id, dst_id, kind, COALESCE(file, ''), COALESCE(line, -1));
"""


def normalize_workspace(path: str) -> str:
    """Canonicalise a workspace root so ``C:\\Foo`` and ``c:/foo`` are one key.

    ``Path.resolve()`` alone is not enough on Windows: it fixes ``..`` and the
    drive letter but preserves the caller's casing. NTFS is case-insensitive,
    so ``C:\\Foo`` and ``c:\\foo`` are the same directory and MUST map to one
    workspace, otherwise every (workspace, file) lookup silently misses and
    the "auto-invalidate on hash change" path breaks for half the callers.
    We therefore resolve, then casefold, then normalise separators.

    This is deliberately NOT ``Aurora.safe_resolve_path``: that function is a
    *containment* check (``is_relative_to``) against path traversal. Mixing the
    two would be a bug — containment must compare the caller's original path
    against the real root, which lowercasing would defeat.
    """
    if not path or not path.strip():
        raise ValueError("workspace path must be a non-empty string")
    resolved = os.path.realpath(os.path.abspath(path))
    return resolved.replace("\\", "/").rstrip("/").casefold()


def normalize_relpath(path: str) -> str:
    """Canonicalise a workspace-relative file path for storage and lookups.

    Rejects absolute paths and drive-letter forms: a relative path is the
    contract, and allowing absolute paths here would let two spellings of the
    same file live under different ``file`` values (breaking content_hash
    invalidation and the callers query). Case is folded and separators are
    flattened to ``/`` for the same Windows reason as ``normalize_workspace``.
    """
    if not path or not path.strip():
        raise ValueError("file path must be a non-empty string")
    raw = path.strip()
    if raw.startswith(("/", "\\")) or (len(raw) >= 2 and raw[1] == ":"):
        raise ValueError(
            f"file path must be workspace-relative, got absolute: {path!r}"
        )
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"file path must not escape the workspace: {path!r}")
    return "/".join(parts).casefold()


def content_hash(text: str) -> str:
    """Hash file text for the index-validity mechanism.

    sha256 truncated to 16 hex chars, matching CONTEXT_PAGING.md §6.1
    ("sha256 前 16 位") and ``symbols.file_content_hash``. The algorithm is not
    a free choice here: ``file_content.content_hash`` and the hash callers pass
    to ``query_symbols`` are compared for equality, so any producer with a
    different digest would silently fail the validity check. UTF-8 encoding
    keeps the digest stable across platforms and locales.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def symbol_id(workspace: str, relpath: str, qualified_name: str) -> str:
    """Build the stable symbol id ``{workspace}::{relpath}::{qualified_name}``.

    The workspace is part of the id because the id is the symbols table's
    PRIMARY KEY: leaving it out would merge same-named symbols from two
    workspaces into one row. It also gives ``delete_edges_for_file`` an exact
    prefix to scope on. Line numbers are excluded — they drift across edits, and
    a drifting primary key would break Phase 4 incremental rebuilds and every
    edge pointing at it. ``normalize_workspace`` is idempotent, so passing an
    already-canonical workspace here is safe and the result is stable.
    """
    ws = normalize_workspace(workspace)
    rel = normalize_relpath(relpath)
    if not qualified_name:
        raise ValueError("qualified_name must be non-empty")
    return f"{ws}::{rel}::{qualified_name}"
