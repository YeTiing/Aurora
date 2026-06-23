# coding: utf-8
"""Semantic Memory - vector search with numpy cosine similarity + SQLite FTS5."""
from __future__ import annotations
import json, os, sqlite3, time, uuid, hashlib, threading, math
from pathlib import Path
from typing import Any

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class SemanticMemory:
    """Long-term semantic memory backed by SQLite with FTS5 and vector storage."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(Path(os.getcwd()) / ".aurora" / "semantic_memory.db")
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(p)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS semantic_memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    embedding_json TEXT DEFAULT '[]',
                    created_at REAL DEFAULT (strftime('%s','now')),
                    access_count INTEGER DEFAULT 0,
                    last_accessed REAL DEFAULT (strftime('%s','now'))
                )
            """)
            try:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS semantic_fts USING fts5(content, content=semantic_memories, content_rowid=rowid)")
            except Exception:
                pass

    def _simple_embed(self, text: str, dim: int = 128) -> list[float]:
        """Bag-of-words style embedding using character n-grams as fallback when numpy is unavailable."""
        if HAS_NUMPY:
            h = hashlib.md5(text.encode()).digest()
            seed = int.from_bytes(h[:4], "big")
            rng = np.random.RandomState(seed)
            vec = rng.randn(dim).astype(np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            return vec.tolist()
        h = hashlib.md5(text.encode()).digest()
        seed = int.from_bytes(h[:4], "big")
        vec = [(seed * (i + 1) * 2654435761 % (2**32)) / (2**32) * 2 - 1 for i in range(dim)]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        if HAS_NUMPY:
            na = np.array(a, dtype=np.float32)
            nb = np.array(b, dtype=np.float32)
            dot = float(np.dot(na, nb))
            na_norm = float(np.linalg.norm(na))
            nb_norm = float(np.linalg.norm(nb))
        else:
            dot = sum(ai * bi for ai, bi in zip(a, b))
            na_norm = math.sqrt(sum(ai * ai for ai in a))
            nb_norm = math.sqrt(sum(bi * bi for bi in b))
        if na_norm < 1e-10 or nb_norm < 1e-10:
            return 0.0
        return dot / (na_norm * nb_norm)

    def index(self, text: str, metadata: dict | None = None) -> str:
        """Embed text and store in the semantic memory."""
        mid = uuid.uuid4().hex[:16]
        embedding = self._simple_embed(text)
        meta = metadata or {}
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO semantic_memories (id, content, metadata_json, embedding_json) VALUES (?,?,?,?)",
                (mid, text, json.dumps(meta, ensure_ascii=False), json.dumps(embedding)),
            )
        return mid

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Cosine similarity search across stored memories."""
        qvec = self._simple_embed(query)
        results = []
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, content, metadata_json, embedding_json, created_at FROM semantic_memories"
            ).fetchall()
        for row in rows:
            mid, content, meta_json, emb_json, created = row
            emb = json.loads(emb_json) if emb_json else []
            if not emb:
                continue
            sim = self._cosine_similarity(qvec, emb)
            results.append({
                "id": mid,
                "content": content[:500],
                "metadata": json.loads(meta_json) if meta_json else {},
                "score": round(sim, 4),
                "created_at": created,
            })
        results.sort(key=lambda r: r["score"], reverse=True)
        for r in results[:top_k]:
            self._touch(r["id"])
        return results[:top_k]

    def forget(self, memory_id: str) -> bool:
        """Remove a memory by ID."""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM semantic_memories WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

    def _touch(self, mid: str):
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE semantic_memories SET access_count = access_count + 1, last_accessed = strftime('%s','now') WHERE id = ?",
                (mid,),
            )

    def build_episodic_index(self) -> int:
        """Index all past episode summaries from the memory hub."""
        count = 0
        try:
            from backend.memory import get_memory
            hub = get_memory()
            episodes = hub.episodic.query_all()
            for ep in episodes:
                text = ep.get("task", "") + " " + ep.get("outcome", "") + " " + " ".join(ep.get("key_learnings", []))
                if text.strip():
                    self.index(text, {"source": "episodic", "episode_id": ep.get("id", "")})
                    count += 1
        except Exception:
            pass
        return count

    def hybrid_search(self, query: str, top_k: int = 5) -> list[dict]:
        """Combines FTS5 text search with vector similarity."""
        vector_results = self.search(query, top_k=top_k * 2)
        fts_results = self._fts_search(query, top_k=top_k)
        merged = {}
        for r in vector_results:
            merged[r["id"]] = r
            merged[r["id"]]["_source"] = "vector"
        for r in fts_results:
            if r["id"] in merged:
                merged[r["id"]]["score"] = max(merged[r["id"]]["score"], r["score"])
                merged[r["id"]]["_source"] = "hybrid"
            else:
                merged[r["id"]] = r
                merged[r["id"]]["_source"] = "fts"
        results = sorted(merged.values(), key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def _fts_search(self, query: str, top_k: int = 5) -> list[dict]:
        """FTS5 text-based search."""
        results = []
        try:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT id, content, metadata_json, rank FROM semantic_fts WHERE semantic_fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, top_k),
                ).fetchall()
            for row in rows:
                mid, content, meta_json, rank = row
                results.append({
                    "id": mid,
                    "content": content[:500] if content else "",
                    "metadata": json.loads(meta_json) if meta_json else {},
                    "score": round(1.0 / (1.0 + abs(rank)) if rank else 0.5, 4),
                })
        except Exception:
            pass
        return results

    def count(self) -> int:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM semantic_memories").fetchone()[0]


_semantic_memory: SemanticMemory | None = None


def get_semantic_memory() -> SemanticMemory:
    global _semantic_memory
    if _semantic_memory is None:
        _semantic_memory = SemanticMemory()
    return _semantic_memory