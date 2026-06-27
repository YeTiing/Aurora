"""RE Session - SQLite-based storage for captured requests, interactions, JS hooks."""
from __future__ import annotations
import sqlite3, json, time, uuid, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import logging
logger = logging.getLogger("aurora")

DB_DIR = Path("re_data/sessions")
DB_DIR.mkdir(parents=True, exist_ok=True)

@dataclass
class CapturedRequest:
    id: str
    session_id: str
    seq: int
    method: str = "GET"
    url: str = ""
    host: str = ""
    path: str = ""
    request_headers: str = "{}"
    request_body: str = ""
    response_status: int = 0
    response_headers: str = "{}"
    response_body: str = ""
    content_type: str = ""
    is_static: bool = False
    is_js: bool = False
    is_streaming: bool = False
    duration_ms: float = 0
    captured_at: float = field(default_factory=time.time)

    @property
    def headers_dict(self) -> dict: return json.loads(self.request_headers) if self.request_headers else {}
    @property
    def resp_headers_dict(self) -> dict: return json.loads(self.response_headers) if self.response_headers else {}

    def to_dict(self) -> dict:
        return {
            "id": self.id, "seq": self.seq, "method": self.method, "url": self.url,
            "host": self.host, "path": self.path, "status": self.response_status,
            "content_type": self.content_type, "is_static": self.is_static,
            "is_js": self.is_js, "is_streaming": self.is_streaming,
            "duration_ms": self.duration_ms, "captured_at": self.captured_at,
            "request_headers": self.headers_dict, "request_body": self.request_body[:5000],
            "response_headers": self.resp_headers_dict, "response_body": self.response_body[:5000],
        }

STATIC_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".webm", ".pdf", ".zip"}

def is_static_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in STATIC_EXTENSIONS)

def is_js_url(url: str, content_type: str = "") -> bool:
    if "javascript" in (content_type or ""): return True
    path = urlparse(url).path.lower()
    return path.endswith(".js") or path.endswith(".mjs")


class RESession:
    def __init__(self, session_id: str):
        self.id = session_id
        self.db_path = DB_DIR / f"{session_id}.db"
        self._conn: sqlite3.Connection | None = None
        self.seq = 0
        self.url = ""
        self.scene = "unknown"

    def open(self):
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, session_id TEXT, seq INTEGER,
                method TEXT, url TEXT, host TEXT, path TEXT,
                request_headers TEXT, request_body TEXT,
                response_status INTEGER, response_headers TEXT, response_body TEXT,
                content_type TEXT, is_static INTEGER, is_js INTEGER,
                is_streaming INTEGER, duration_ms REAL, captured_at REAL
            );
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                timestamp REAL, action_type TEXT, selector TEXT,
                tag_name TEXT, element_text TEXT, value TEXT,
                scroll_x REAL, scroll_y REAL, path_json TEXT, url TEXT
            );
            CREATE TABLE IF NOT EXISTS js_hooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                hook_type TEXT, function_name TEXT, arguments TEXT,
                result TEXT, stack TEXT, ts REAL
            );
            CREATE TABLE IF NOT EXISTS storage_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                timestamp REAL, cookies TEXT, local_storage TEXT,
                session_storage TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_req_session ON requests(session_id, seq);
            CREATE INDEX IF NOT EXISTS idx_hooks_session ON js_hooks(session_id);
        """)
        self._conn.commit()
        return self

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if not self._conn: self.open()
        return self._conn

    def add_request(self, req: CapturedRequest):
        self.conn.execute("""INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            req.id, self.id, req.seq, req.method, req.url, req.host, req.path,
            json.dumps(req.headers_dict, ensure_ascii=False), req.request_body[:100000],
            req.response_status, json.dumps(req.resp_headers_dict, ensure_ascii=False), req.response_body[:100000],
            req.content_type, int(req.is_static), int(req.is_js),
            int(req.is_streaming), req.duration_ms, req.captured_at
        ))
        self.seq = req.seq

    def add_hook(self, hook_type: str, func_name: str, args: str = "", result: str = "", stack: str = ""):
        self.conn.execute("INSERT INTO js_hooks VALUES (NULL,?,?,?,?,?,?)", (
            self.id, time.time(), hook_type, func_name, args, result, stack
        ))
        self.conn.commit()

    def add_interaction(self, atype: str, selector: str = "", tag: str = "", text: str = "", value: str = "", sx: float = 0, sy: float = 0, path_json: str = "", url: str = ""):
        self.conn.execute("INSERT INTO interactions VALUES (NULL,?,?,?,?,?,?,?,?,?,?)", (
            self.id, time.time(), atype, selector, tag, text, value, sx, sy, path_json, url
        ))
        self.conn.commit()

    def get_requests(self, api_only: bool = False) -> list[dict]:
        q = "SELECT * FROM requests WHERE session_id=? ORDER BY seq"
        if api_only: q = "SELECT * FROM requests WHERE session_id=? AND is_static=0 ORDER BY seq"
        return [dict(r) for r in self.conn.execute(q, (self.id,))]

    def get_api_endpoints(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT path FROM requests WHERE session_id=? AND is_static=0", (self.id,))
        return [r[0] for r in rows if r[0]]

    def get_hooks(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM js_hooks WHERE session_id=?", (self.id,))]

    def get_request_detail(self, req_id: str) -> dict | None:
        """Get full request with body."""
        r = self.conn.execute("SELECT * FROM requests WHERE id=? AND session_id=?", (req_id, self.id)).fetchone()
        return dict(r) if r else None

    def stats(self) -> dict:
        r = self.conn.execute("SELECT COUNT(*), SUM(CASE WHEN is_static=0 THEN 1 ELSE 0 END), SUM(CASE WHEN is_js=1 THEN 1 ELSE 0 END) FROM requests WHERE session_id=?", (self.id,)).fetchone()
        h = self.conn.execute("SELECT COUNT(*) FROM js_hooks WHERE session_id=?", (self.id,)).fetchone()
        return {"total": r[0] or 0, "apis": r[1] or 0, "js_files": r[2] or 0, "hooks": h[0] or 0}


class RESessionManager:
    def __init__(self):
        self._sessions: dict[str, RESession] = {}
        self._active: str | None = None

    def create(self, url: str = "") -> RESession:
        sid = time.strftime("re_%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        sess = RESession(sid)
        sess.url = url
        sess.open()
        self._sessions[sid] = sess
        self._active = sid
        return sess

    def get(self, sid: str) -> RESession | None:
        if sid not in self._sessions:
            db_path = DB_DIR / f"{sid}.db"
            if db_path.exists():
                sess = RESession(sid)
                sess.open()
                self._sessions[sid] = sess
        return self._sessions.get(sid)

    def list_sessions(self) -> list[dict]:
        results = []
        for db in sorted(DB_DIR.glob("*.db"), reverse=True):
            sid = db.stem
            sess = self.get(sid)
            if sess:
                s = sess.stats()
                results.append({"id": sid, "url": sess.url, "scene": sess.scene, **s, "db_size": db.stat().st_size})
        return results

    def delete(self, sid: str) -> bool:
        s = self._sessions.pop(sid, None)
        if s:
            try: s.conn.close()
            except Exception: logger.debug('re session load failed', exc_info=True)
            s._conn = None
        db_path = DB_DIR / f"{sid}.db"
        wal = DB_DIR / f"{sid}.db-wal"
        shm = DB_DIR / f"{sid}.db-shm"
        if db_path.exists(): db_path.unlink()
        if wal.exists(): wal.unlink()
        if shm.exists(): shm.unlink()
        return True

_mgr: RESessionManager | None = None
def get_re_manager() -> RESessionManager:
    global _mgr
    if _mgr is None: _mgr = RESessionManager()
    return _mgr
