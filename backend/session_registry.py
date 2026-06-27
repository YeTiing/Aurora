"""Session registry - thread-safe session metadata tracking.

Provides a single source of truth for active session metadata,
shared between chat.py (writer) and files.py (reader).
"""
from __future__ import annotations
import threading
import time

_lock = threading.Lock()
_meta: dict[str, dict] = {}

def track(session_id: str, workspace: str = ".") -> None:
    """Record a session as active with current timestamp."""
    with _lock:
        _meta[session_id] = {
            "session_id": session_id,
            "workspace": workspace,
            "last_seen": time.time(),
        }

def list_active(max_age_sec: float = 3600.0) -> list[dict]:
    """Return sessions active within max_age_sec."""
    now = time.time()
    with _lock:
        return [
            m for m in list(_meta.values())
            if now - m.get("last_seen", 0) < max_age_sec
        ]

def count() -> int:
    """Return count of tracked sessions."""
    with _lock:
        return len(_meta)

def cleanup(max_age_sec: float = 3600.0) -> int:
    """Remove stale entries, return count of removed."""
    now = time.time()
    with _lock:
        stale = [k for k, v in _meta.items() if now - v.get("last_seen", 0) >= max_age_sec]
        for k in stale:
            del _meta[k]
        return len(stale)
