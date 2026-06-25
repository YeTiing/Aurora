# Aurora SQLite — Codex对齐版：threads / goals / agent_jobs / logs / memories / state
from __future__ import annotations
import sqlite3, json, time, os, threading, uuid
from pathlib import Path
from typing import Any

class SQLiteDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.path))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]):
        return self.conn.executemany(sql, params_list)

    def commit(self):
        self.conn.commit()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# ═══════════════════════════════════════════════════════════════
# Threads DB — Codex threads表全对齐
# ═══════════════════════════════════════════════════════════════

class ThreadsDB:
    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "threads.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                workspace TEXT DEFAULT '',
                agent_nickname TEXT DEFAULT '',
                agent_role TEXT DEFAULT '',
                agent_path TEXT DEFAULT '',
                model TEXT DEFAULT '',
                reasoning_effort TEXT DEFAULT '',
                memory_mode TEXT DEFAULT '',
                preview TEXT DEFAULT '',
                first_user_message TEXT DEFAULT '',
                thread_source TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at REAL,
                updated_at REAL,
                recency_at_ms INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_threads_recency ON threads(recency_at_ms)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_threads_agent ON threads(agent_nickname)")
        self.db.commit()

    def create(self, **kwargs) -> dict:
        tid = kwargs.pop("id", uuid.uuid4().hex[:16])
        now = time.time()
        fields = ["id", "created_at", "updated_at", "recency_at_ms"]
        values = [tid, now, now, int(now * 1000)]
        for k, v in kwargs.items():
            if k in ["title", "workspace", "agent_nickname", "agent_role", "agent_path",
                      "model", "reasoning_effort", "memory_mode", "preview",
                      "first_user_message", "thread_source", "status", "metadata"]:
                fields.append(k)
                values.append(v if isinstance(v, str) else json.dumps(v, default=str))
        placeholders = ", ".join("?" * len(fields))
        self.db.execute(
            f"INSERT INTO threads ({', '.join(fields)}) VALUES ({placeholders})",
            tuple(values)
        )
        self.db.commit()
        return self.get(tid)

    def get(self, thread_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None

    def update(self, thread_id: str, **kwargs):
        if not kwargs:
            return
        kwargs["updated_at"] = time.time()
        kwargs["recency_at_ms"] = int(time.time() * 1000)
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self.db.execute(
            f"UPDATE threads SET {sets} WHERE id = ?",
            tuple(kwargs.values()) + (thread_id,)
        )
        self.db.commit()

    def list_recent(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM threads WHERE status = 'active' ORDER BY recency_at_ms DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def set_agent(self, thread_id: str, nickname: str = "", role: str = "", model: str = ""):
        self.update(thread_id, agent_nickname=nickname, agent_role=role, model=model)

    def delete(self, thread_id: str):
        self.db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        self.db.commit()

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) as c FROM threads").fetchone()["c"]
        active = self.db.execute("SELECT COUNT(*) as c FROM threads WHERE status='active'").fetchone()["c"]
        return {"total": total, "active": active}


# ═══════════════════════════════════════════════════════════════
# Thread Goals — Codex thread_goals表
# ═══════════════════════════════════════════════════════════════

class ThreadGoalsDB:
    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "thread_goals.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS thread_goals (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                objective TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                token_budget INTEGER,
                tokens_used INTEGER DEFAULT 0,
                time_used_seconds INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL,
                completed_at REAL,
                metadata TEXT
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_tg_thread ON thread_goals(thread_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_tg_status ON thread_goals(status)")
        self.db.commit()

    def create(self, goal_id: str, thread_id: str, objective: str, token_budget: int | None = None) -> dict:
        now = time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO thread_goals (id, thread_id, objective, status, token_budget, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?)",
            (goal_id, thread_id, objective, token_budget, now, now)
        )
        self.db.commit()
        return self.get(goal_id)

    def get(self, goal_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM thread_goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None

    def get_active_for_thread(self, thread_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM thread_goals WHERE thread_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (thread_id,)
        ).fetchone()
        return dict(row) if row else None

    def update(self, goal_id: str, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [time.time(), goal_id]
        self.db.execute(f"UPDATE thread_goals SET {sets}, updated_at = ? WHERE id = ?", values)
        self.db.commit()

    def complete(self, goal_id: str, tokens_used: int = 0, time_used: int = 0):
        now = time.time()
        self.db.execute(
            "UPDATE thread_goals SET status='complete', tokens_used=?, time_used_seconds=?, completed_at=?, updated_at=? WHERE id=?",
            (tokens_used, time_used, now, now, goal_id)
        )
        self.db.commit()

    def block(self, goal_id: str):
        self.update(goal_id, status="blocked")

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) as c FROM thread_goals").fetchone()["c"]
        completed = self.db.execute("SELECT COUNT(*) as c FROM thread_goals WHERE status='complete'").fetchone()["c"]
        return {"total": total, "completed": completed}


# ═══════════════════════════════════════════════════════════════
# Agent Jobs — Codex agent_jobs + agent_job_items表
# ═══════════════════════════════════════════════════════════════

class AgentJobsDB:
    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "agent_jobs.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                parent_agent_id TEXT DEFAULT '',
                agent_type TEXT DEFAULT 'default',
                agent_model TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                priority INTEGER DEFAULT 0,
                created_at REAL,
                started_at REAL,
                completed_at REAL,
                error TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}'
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_aj_thread ON agent_jobs(thread_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_aj_status ON agent_jobs(status)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_aj_parent ON agent_jobs(parent_agent_id)")

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS agent_job_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT DEFAULT '[]',
                tool_call_id TEXT DEFAULT '',
                created_at REAL,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (job_id) REFERENCES agent_jobs(id)
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_aji_job ON agent_job_items(job_id)")
        self.db.commit()

    def create_job(self, thread_id: str, agent_type: str = "default", **kwargs) -> str:
        job_id = uuid.uuid4().hex[:16]
        now = time.time()
        self.db.execute(
            "INSERT INTO agent_jobs (id, thread_id, agent_type, agent_model, status, parent_agent_id, created_at, metadata) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (job_id, thread_id, agent_type, kwargs.get("agent_model", ""), kwargs.get("parent_agent_id", ""), now, json.dumps(kwargs.get("metadata", {})))
        )
        self.db.commit()
        return job_id

    def add_item(self, job_id: str, role: str, content: str, seq: int = 0, tool_calls: list | None = None):
        if seq == 0:
            max_seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) as m FROM agent_job_items WHERE job_id = ?", (job_id,)).fetchone()["m"]
            seq = max_seq + 1
        self.db.execute(
            "INSERT INTO agent_job_items (job_id, seq, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, seq, role, content, json.dumps(tool_calls or []), time.time())
        )
        self.db.commit()

    def get_items(self, job_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM agent_job_items WHERE job_id = ? ORDER BY seq", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_status(self, job_id: str, status: str, error: str = ""):
        now = time.time()
        if status == "running":
            self.db.execute("UPDATE agent_jobs SET status=?, started_at=? WHERE id=?", (status, now, job_id))
        elif status in ("completed", "failed"):
            self.db.execute("UPDATE agent_jobs SET status=?, completed_at=?, error=? WHERE id=?", (status, now, error, job_id))
        else:
            self.db.execute("UPDATE agent_jobs SET status=? WHERE id=?", (status, job_id))
        self.db.commit()

    def list_pending(self, limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM agent_jobs WHERE status IN ('pending', 'running') ORDER BY priority DESC, created_at LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) as c FROM agent_jobs").fetchone()["c"]
        by_status = {}
        for row in self.db.execute("SELECT status, COUNT(*) as c FROM agent_jobs GROUP BY status").fetchall():
            by_status[row["status"]] = row["c"]
        return {"total": total, **by_status}


# ═══════════════════════════════════════════════════════════════
# Thread Spawn Edges — Codex thread_spawn_edges (Agent DAG)
# ═══════════════════════════════════════════════════════════════

class SpawnEdgesDB:
    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "spawn_edges.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS thread_spawn_edges (
                parent_thread_id TEXT NOT NULL,
                child_thread_id TEXT NOT NULL,
                spawn_type TEXT DEFAULT 'spawn',
                created_at REAL,
                metadata TEXT DEFAULT '{}',
                PRIMARY KEY (parent_thread_id, child_thread_id)
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_tse_parent ON thread_spawn_edges(parent_thread_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_tse_child ON thread_spawn_edges(child_thread_id)")
        self.db.commit()

    def add_edge(self, parent_id: str, child_id: str, spawn_type: str = "spawn", metadata: dict | None = None):
        self.db.execute(
            "INSERT OR IGNORE INTO thread_spawn_edges (parent_thread_id, child_thread_id, spawn_type, created_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (parent_id, child_id, spawn_type, time.time(), json.dumps(metadata or {}))
        )
        self.db.commit()

    def get_children(self, parent_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM thread_spawn_edges WHERE parent_thread_id = ? ORDER BY created_at",
            (parent_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_parent(self, child_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM thread_spawn_edges WHERE child_thread_id = ?",
            (child_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_full_tree(self, root_id: str) -> dict:
        """递归获取完整DAG树"""
        children = self.get_children(root_id)
        return {
            "thread_id": root_id,
            "children": [self.get_full_tree(c["child_thread_id"]) for c in children]
        }


# ═══════════════════════════════════════════════════════════════
# Logs / Memories / State (保持兼容)
# ═══════════════════════════════════════════════════════════════

class LogsDB:
    LEVELS = {"debug": 0, "info": 1, "warn": 2, "error": 3}

    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "logs.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                level TEXT NOT NULL,
                module TEXT DEFAULT '',
                message TEXT NOT NULL,
                thread_id TEXT DEFAULT '',
                process_uuid TEXT DEFAULT '',
                estimated_bytes INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(timestamp)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_logs_thread ON logs(thread_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")
        self.db.commit()

    def log(self, level: str, message: str, module: str = "", thread_id: str = "", metadata: dict | None = None):
        import os as _os
        self.db.execute(
            "INSERT INTO logs (timestamp, level, module, message, thread_id, process_uuid, estimated_bytes, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), level, module, message, thread_id, str(uuid.uuid4()), len(message.encode("utf-8")), json.dumps(metadata or {}))
        )
        self.db.commit()

    def debug(self, msg: str, **kw): self.log("debug", msg, **kw)
    def info(self, msg: str, **kw): self.log("info", msg, **kw)
    def warn(self, msg: str, **kw): self.log("warn", msg, **kw)
    def error(self, msg: str, **kw): self.log("error", msg, **kw)

    def query(self, level: str | None = None, thread_id: str | None = None, limit: int = 100) -> list[dict]:
        conditions = []
        params = []
        if level:
            conditions.append("level = ?"); params.append(level)
        if thread_id:
            conditions.append("thread_id = ?"); params.append(thread_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.db.execute(f"SELECT * FROM logs {where} ORDER BY timestamp DESC LIMIT ?", tuple(params) + (limit,)).fetchall()
        return [dict(r) for r in rows]


class MemoriesDB:
    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "memories.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at REAL,
                updated_at REAL,
                ttl REAL
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")
        self.db.commit()

    def set(self, key: str, value: Any, category: str = "general", ttl: float | None = None):
        now = time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO memories (key, value, category, created_at, updated_at, ttl) VALUES (?, ?, ?, COALESCE((SELECT created_at FROM memories WHERE key=?), ?), ?, ?)",
            (key, json.dumps(value, default=str), category, key, now, now, ttl)
        )
        self.db.commit()

    def get(self, key: str) -> Any | None:
        row = self.db.execute("SELECT value, ttl, updated_at FROM memories WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        if row["ttl"] and time.time() - row["updated_at"] > row["ttl"]:
            self.delete(key)
            return None
        return json.loads(row["value"])

    def delete(self, key: str):
        self.db.execute("DELETE FROM memories WHERE key = ?", (key,))
        self.db.commit()

    def list_keys(self, category: str | None = None) -> list[str]:
        if category:
            rows = self.db.execute("SELECT key FROM memories WHERE category = ?", (category,)).fetchall()
        else:
            rows = self.db.execute("SELECT key FROM memories").fetchall()
        return [r["key"] for r in rows]


class StateDB:
    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "state.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL
            )
        """)
        self.db.commit()

    def set(self, key: str, value: Any):
        self.db.execute("INSERT OR REPLACE INTO state (key, value, updated_at) VALUES (?, ?, ?)",
                        (key, json.dumps(value, default=str), time.time()))
        self.db.commit()

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default


# ═══════════════════════════════════════════════════════════════
# 全局实例
# ═══════════════════════════════════════════════════════════════

_threads_db = None
_thread_goals_db = None
_dynamic_tools_db = None

def get_dynamic_tools_db(base_dir: str | None = None) -> ThreadDynamicToolsDB:
    global _dynamic_tools_db
    if _dynamic_tools_db is None: _dynamic_tools_db = ThreadDynamicToolsDB(base_dir)
    return _dynamic_tools_db



# ──────────────────────────────────────────── Automation Runs DB
class AutomationRunsDB:
    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "automation.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS automation_runs (
                id TEXT PRIMARY KEY,
                automation_id TEXT NOT NULL,
                thread_id TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                thread_title TEXT DEFAULT '',
                source_cwd TEXT DEFAULT '',
                created_at REAL,
                updated_at REAL,
                archived_user_message TEXT DEFAULT '',
                archived_assistant_message TEXT DEFAULT '',
                archived_reason TEXT DEFAULT ''
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_aruns_automation ON automation_runs(automation_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_aruns_status ON automation_runs(status)")
        self.db.commit()

    def create(self, automation_id: str, **kwargs) -> dict:
        rid = kwargs.pop("id", uuid.uuid4().hex[:16])
        now = time.time()
        fields = ["id", "automation_id", "created_at", "updated_at"]
        values = [rid, automation_id, now, now]
        for k, v in kwargs.items():
            if k in ["thread_id", "status", "thread_title", "source_cwd",
                      "archived_user_message", "archived_assistant_message", "archived_reason"]:
                fields.append(k)
                values.append(str(v) if v is not None else "")
        placeholders = ", ".join("?" * len(fields))
        self.db.execute(
            f"INSERT INTO automation_runs ({', '.join(fields)}) VALUES ({placeholders})",
            tuple(values)
        )
        self.db.commit()
        return self.get(rid)

    def get(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def update(self, run_id: str, **kwargs):
        if not kwargs:
            return
        kwargs["updated_at"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self.db.execute(
            f"UPDATE automation_runs SET {sets} WHERE id = ?",
            tuple(kwargs.values()) + (run_id,)
        )
        self.db.commit()

    def list_by_automation(self, automation_id: str, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM automation_runs WHERE automation_id = ? ORDER BY created_at DESC LIMIT ?",
            (automation_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_recent(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM automation_runs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, run_id: str):
        self.db.execute("DELETE FROM automation_runs WHERE id = ?", (run_id,))
        self.db.commit()

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) as c FROM automation_runs").fetchone()
        by_status = self.db.execute(
            "SELECT status, COUNT(*) as c FROM automation_runs GROUP BY status"
        ).fetchall()
        return {
            "total": total["c"] if total else 0,
            "by_status": {r["status"]: r["c"] for r in by_status},
        }


# ──────────────────────────────────────────── Inbox Items DB
class InboxItemsDB:
    def __init__(self, base_dir: str | Path = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self.db = SQLiteDB(base / "inbox.sqlite")
        self._init()

    def _init(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS inbox_items (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                thread_id TEXT DEFAULT '',
                read_at REAL,
                created_at REAL
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_inbox_read ON inbox_items(read_at)")
        self.db.commit()

    def create(self, title: str = "", description: str = "", thread_id: str = "") -> dict:
        iid = uuid.uuid4().hex[:16]
        now = time.time()
        self.db.execute(
            "INSERT INTO inbox_items (id, title, description, thread_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (iid, title, description, thread_id, now)
        )
        self.db.commit()
        return self.get(iid)

    def get(self, item_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def mark_read(self, item_id: str):
        self.db.execute("UPDATE inbox_items SET read_at = ? WHERE id = ?", (time.time(), item_id))
        self.db.commit()

    def list_unread(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM inbox_items WHERE read_at IS NULL ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM inbox_items ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, item_id: str):
        self.db.execute("DELETE FROM inbox_items WHERE id = ?", (item_id,))
        self.db.commit()

    def unread_count(self) -> int:
        row = self.db.execute("SELECT COUNT(*) as c FROM inbox_items WHERE read_at IS NULL").fetchone()
        return row["c"] if row else 0



_agent_jobs_db = None
_spawn_edges_db = None
_logs_db = None
_memories_db = None
_state_db = None
_automation_runs_db = None
_inbox_items_db = None

def get_threads_db(base_dir: str | None = None) -> ThreadsDB:
    global _threads_db
    if _threads_db is None: _threads_db = ThreadsDB(base_dir)
    return _threads_db

def get_thread_goals_db(base_dir: str | None = None) -> ThreadGoalsDB:
    global _thread_goals_db
    if _thread_goals_db is None: _thread_goals_db = ThreadGoalsDB(base_dir)
    return _thread_goals_db

def get_agent_jobs_db(base_dir: str | None = None) -> AgentJobsDB:
    global _agent_jobs_db
    if _agent_jobs_db is None: _agent_jobs_db = AgentJobsDB(base_dir)
    return _agent_jobs_db

def get_spawn_edges_db(base_dir: str | None = None) -> SpawnEdgesDB:
    global _spawn_edges_db
    if _spawn_edges_db is None: _spawn_edges_db = SpawnEdgesDB(base_dir)
    return _spawn_edges_db

def get_logs_db(base_dir: str | None = None) -> LogsDB:
    global _logs_db
    if _logs_db is None: _logs_db = LogsDB(base_dir)
    return _logs_db

def get_memories_db(base_dir: str | None = None) -> MemoriesDB:
    global _memories_db
    if _memories_db is None: _memories_db = MemoriesDB(base_dir)
    return _memories_db

def get_state_db(base_dir: str | None = None) -> StateDB:
    global _state_db
    if _state_db is None: _state_db = StateDB(base_dir)
    return _state_db

def get_automation_runs_db(base_dir: str | None = None) -> AutomationRunsDB:
    global _automation_runs_db
    if _automation_runs_db is None: _automation_runs_db = AutomationRunsDB(base_dir)
    return _automation_runs_db

def get_inbox_items_db(base_dir: str | None = None) -> InboxItemsDB:
    global _inbox_items_db
    if _inbox_items_db is None: _inbox_items_db = InboxItemsDB(base_dir)
    return _inbox_items_db
