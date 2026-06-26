"""Global Session Search - Full-text search across all conversation sessions.

Indexes conversation transcripts for cross-session retrieval using SQLite FTS5.
"""
from __future__ import annotations
import json, os, sqlite3, threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SearchHit:
    session_id: str
    message_id: str
    role: str
    content: str
    timestamp: float = 0.0
    snippet: str = ""
    score: float = 0.0


class SessionSearch:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path(os.getcwd()) / ".aurora" / "session_search.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp REAL DEFAULT (strftime('%s','now')),
                    metadata_json TEXT DEFAULT '{}'
                )
            """)
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS message_fts "
                    "USING fts5(content, session_id, content=messages, content_rowid=rowid)"
                )
            except Exception:
                pass

    def index_message(self, session_id: str, message_id: str,
                      role: str, content: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO messages(id, session_id, role, content) "
                "VALUES(?,?,?,?)",
                (message_id, session_id, role, content[:8192])
            )
            # Rebuild FTS5 index after insert (external content tables need this)
            try:
                conn.execute("INSERT INTO message_fts(message_fts) VALUES('rebuild')")
            except Exception:
                pass

    def search(self, query: str, limit: int = 10,
               session_id: str = None) -> list[SearchHit]:
        with sqlite3.connect(self.db_path) as conn:
            if session_id:
                sql = (
                    "SELECT m.id, m.session_id, m.role, m.content, m.timestamp, "
                    "snippet(message_fts, 0, '<mark>', '</mark>', '...', 40) "
                    "FROM message_fts "
                    "JOIN messages m ON message_fts.rowid = m.rowid "
                    "WHERE message_fts MATCH ? AND m.session_id = ? "
                    "ORDER BY rank LIMIT ?"
                )
                rows = conn.execute(sql, (query, session_id, limit)).fetchall()
            else:
                sql = (
                    "SELECT m.id, m.session_id, m.role, m.content, m.timestamp, "
                    "snippet(message_fts, 0, '<mark>', '</mark>', '...', 40) "
                    "FROM message_fts "
                    "JOIN messages m ON message_fts.rowid = m.rowid "
                    "WHERE message_fts MATCH ? ORDER BY rank LIMIT ?"
                )
                rows = conn.execute(sql, (query, limit)).fetchall()

        hits = []
        for r in rows:
            hits.append(SearchHit(
                message_id=r[0], session_id=r[1], role=r[2],
                content=r[3][:500], timestamp=r[4],
                snippet=r[5] or r[3][:200],
            ))
        return hits

    def delete_session(self, session_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    def recent(self, limit: int = 10) -> list[dict]:
        """Get recently indexed messages."""
        import sqlite3
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT session_id, role, snippet, timestamp FROM messages ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [{"session_id": r["session_id"], "role": r["role"],
                     "snippet": r["snippet"], "timestamp": r["timestamp"]} for r in rows]
        except Exception:
            return []

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            sessions = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM messages"
            ).fetchone()[0]
            return {"total_messages": total, "indexed_sessions": sessions}


session_search = SessionSearch()
