# -*- coding: utf-8 -*-
"""Session Transcript Index — full-text search over JSONL session files.

Port of cc-haha's session transcript search.
Builds an in-memory index over rollout-*.jsonl files for fast full-text search.
Supports: keyword search, regex search, time-range filtering, session listing.
"""

from __future__ import annotations
import json, os, re, time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Iterator


@dataclass
class TranscriptHit:
    session_id: str = ""
    filepath: str = ""
    line_number: int = 0
    timestamp: str = ""
    event_type: str = ""
    content_preview: str = ""
    score: float = 0.0


@dataclass
class TranscriptSession:
    session_id: str = ""
    filepath: str = ""
    line_count: int = 0
    size_bytes: int = 0
    first_event_at: str = ""
    last_event_at: str = ""
    cwd: str = ""
    event_types: list[str] = field(default_factory=list)


class TranscriptIndex:
    """Full-text search index over session transcript files."""

    def __init__(self, sessions_dir: str = ".aurora/sessions", memory_dir: str = ".aurora/memory"):
        self.sessions_dir = Path(sessions_dir)
        self.memory_dir = Path(memory_dir)
        self._index: dict[str, list[tuple[str, int, str]]] = defaultdict(list)  # session_id -> [(filepath, line, content)]
        self._sessions: dict[str, TranscriptSession] = {}
        self._built = False
        self._last_build = 0.0

    def build(self, force: bool = False) -> int:
        """Build/rebuild the search index. Returns number of sessions indexed."""
        now = time.time()
        if self._built and not force and (now - self._last_build) < 300:
            return len(self._sessions)

        self._index.clear()
        self._sessions.clear()
        count = 0

        # Scan all rollout-*.jsonl files
        sessions_base = self.sessions_dir
        if not sessions_base.exists():
            self._built = True
            self._last_build = now
            return 0

        for root, dirs, files in os.walk(str(sessions_base)):
            for fname in sorted(files):
                if "rollout-" in fname and fname.endswith(".jsonl"):
                    fpath = os.path.join(root, fname)
                    session_id = fname.replace("rollout-", "").replace(".jsonl", "")
                    self._index_session(fpath, session_id)
                    count += 1

        self._built = True
        self._last_build = now
        return count

    def _index_session(self, filepath: str, session_id: str) -> None:
        """Index a single session transcript file."""
        try:
            stat = os.stat(filepath)
            lines = []
            first_ts = ""
            last_ts = ""
            event_types = []
            cwd = ""

            with open(filepath, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    lines.append((i + 1, line))
                    try:
                        evt = json.loads(line)
                        if isinstance(evt, dict):
                            ts = evt.get("timestamp", "")
                            etype = evt.get("type", evt.get("event_type", ""))
                            if ts:
                                if not first_ts:
                                    first_ts = ts
                                last_ts = ts
                            if etype and etype not in event_types:
                                event_types.append(etype)
                            if not cwd:
                                cwd = evt.get("cwd", evt.get("payload", {}).get("cwd", ""))
                    except json.JSONDecodeError:
                        pass

            # Store in index
            session_lines = []
            for line_no, line_text in lines:
                self._index[session_id].append((filepath, line_no, line_text))
                session_lines.append(line_text)

            self._sessions[session_id] = TranscriptSession(
                session_id=session_id,
                filepath=filepath,
                line_count=len(lines),
                size_bytes=stat.st_size,
                first_event_at=first_ts,
                last_event_at=last_ts,
                cwd=str(cwd) if cwd else "",
                event_types=event_types,
            )
        except Exception:
            pass

    def search(self, query: str, limit: int = 20, session_id: str = "", regex: bool = False) -> list[TranscriptHit]:
        """Full-text search across indexed sessions."""
        if not self._built:
            self.build()

        hits: list[TranscriptHit] = []

        if regex:
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error:
                return [TranscriptHit(content_preview=f"Invalid regex: {query}")]
        else:
            pattern = None

        sessions_to_search = [session_id] if session_id else list(self._index.keys())

        for sid in sessions_to_search:
            entries = self._index.get(sid, [])
            for filepath, line_no, line_text in entries:
                if pattern:
                    if pattern.search(line_text):
                        hits.append(self._make_hit(sid, filepath, line_no, line_text))
                else:
                    if query.lower() in line_text.lower():
                        hits.append(self._make_hit(sid, filepath, line_no, line_text))

        # Score by recency (newer sessions first) and rank
        hits.sort(key=lambda h: (h.session_id, -h.line_number), reverse=True)
        return hits[:limit]

    def _make_hit(self, sid: str, fp: str, line_no: int, text: str) -> TranscriptHit:
        preview = text[:300]
        ts = ""
        etype = ""
        try:
            evt = json.loads(text)
            if isinstance(evt, dict):
                ts = evt.get("timestamp", "")
                etype = evt.get("type", evt.get("event_type", ""))
                preview = evt.get("payload", {}).get("output", text[:300])
                if isinstance(preview, dict):
                    preview = json.dumps(preview)[:300]
                elif isinstance(preview, str):
                    preview = preview[:300]
        except Exception:
            pass
        return TranscriptHit(
            session_id=sid, filepath=fp, line_number=line_no,
            timestamp=ts, event_type=etype, content_preview=str(preview),
            score=0.0,
        )

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """List all indexed sessions."""
        if not self._built:
            self.build()
        sessions = sorted(self._sessions.values(), key=lambda s: s.last_event_at, reverse=True)
        result = []
        for s in sessions[:limit]:
            result.append({
                "session_id": s.session_id,
                "line_count": s.line_count,
                "size_bytes": s.size_bytes,
                "first_event": s.first_event_at,
                "last_event": s.last_event_at,
                "cwd": s.cwd,
                "event_types": s.event_types[:10],
            })
        return result

    def get_session(self, session_id: str) -> dict | None:
        """Get session metadata."""
        if not self._built:
            self.build()
        s = self._sessions.get(session_id)
        if not s:
            return None
        return {
            "session_id": s.session_id,
            "filepath": s.filepath,
            "line_count": s.line_count,
            "size_bytes": s.size_bytes,
            "first_event": s.first_event_at,
            "last_event": s.last_event_at,
            "cwd": s.cwd,
            "event_types": s.event_types,
        }

    def stats(self) -> dict:
        if not self._built:
            self.build()
        total_lines = sum(s.line_count for s in self._sessions.values())
        total_bytes = sum(s.size_bytes for s in self._sessions.values())
        return {
            "sessions": len(self._sessions),
            "total_lines": total_lines,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "index_built": self._built,
        }


_index_singleton: Optional[TranscriptIndex] = None

def get_transcript_index() -> TranscriptIndex:
    global _index_singleton
    if _index_singleton is None:
        _index_singleton = TranscriptIndex()
    return _index_singleton
