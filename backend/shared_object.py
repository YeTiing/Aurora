"""
Aurora Shared Object System — Cross-process/module state synchronization.

- SharedObjectRepository: in-memory pub/sub store
- SharedObjectStore: JSON-file-backed persistent store with known keys and watch()
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# ─── Constants ───

_SHARED_STATE_DIR = Path(os.environ.get("AURORA_HOME", ".aurora"))
_SHARED_STATE_FILE = _SHARED_STATE_DIR / "shared_state.json"

# Known keys from Codex — defaults used when no persisted value exists
_KNOWN_KEYS: dict[str, Any] = {
    "pending_worktrees": [],
    "remote_ssh_connections": {},
    "remote_wsl_connections": {},
    "remote_control_connections": {},
    "host_config": {},
    "codex_chronicle_config": {},
}

# ─── Update / Subscription Types ───

@dataclass(frozen=True)
class SharedObjectUpdate:
    key: str
    value: Any
    source: str = ""
    version: int = 1
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": copy.deepcopy(self.value),
            "source": self.source,
            "version": self.version,
            "timestamp": self.timestamp,
        }


Subscriber = Callable[[SharedObjectUpdate], None]
MISSING = object()


# ─── In-Memory Repository ───

class SharedObjectRepository:
    """In-memory pub/sub shared object store."""

    def __init__(self):
        self._objects: dict[str, Any] = {}
        self._versions: dict[str, int] = {}
        self._subscribers: dict[str, list[Subscriber]] = {}
        self._lock = threading.RLock()

    def set(self, key: str, value: Any, source: str = "") -> SharedObjectUpdate:
        with self._lock:
            version = self._versions.get(key, 0) + 1
            stored_value = copy.deepcopy(value)
            self._objects = {**self._objects, key: stored_value}
            self._versions = {**self._versions, key: version}
            update = SharedObjectUpdate(key=key, value=copy.deepcopy(stored_value), source=source, version=version)
            subscribers = [*self._subscribers.get(key, []), *self._subscribers.get("*", [])]
        self._notify(update, subscribers)
        return update

    def get(self, key: str, default: Any = MISSING) -> Any:
        with self._lock:
            if key not in self._objects:
                if default is MISSING:
                    return MISSING
                return copy.deepcopy(default)
            return copy.deepcopy(self._objects[key])

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._objects)

    def subscribe(self, key: str, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers = {
                **self._subscribers,
                key: [*self._subscribers.get(key, []), callback],
            }

    def unsubscribe(self, key: str, callback: Subscriber) -> None:
        with self._lock:
            callbacks = [item for item in self._subscribers.get(key, []) if item != callback]
            if callbacks:
                self._subscribers = {**self._subscribers, key: callbacks}
            else:
                self._subscribers = {item_key: items for item_key, items in self._subscribers.items() if item_key != key}

    def _notify(self, update: SharedObjectUpdate, subscribers: list[Subscriber]) -> None:
        for callback in subscribers:
            try:
                callback(update)
            except Exception:
                pass


# ─── Persistent Store with watch() ───

class SharedObjectStore:
    """JSON-file-backed persistent shared state store.

    - Persists to .aurora/shared_state.json across restarts
    - In-memory cache for fast reads
    - Known Codex keys with sensible defaults
    - Thread-safe with locks
    - watch() for change notifications
    """

    def __init__(self, state_file: Optional[Path] = None):
        self._state_file = state_file or _SHARED_STATE_FILE
        self._cache: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._watchers: dict[str, list[Callable[[str, Any, Any], None]]] = {}
        self._load()

    # ── Persistence ──

    def _load(self) -> None:
        """Load state from JSON file, falling back to known-key defaults."""
        loaded = {}
        if self._state_file.exists():
            try:
                raw = self._state_file.read_text(encoding="utf-8")
                loaded = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, OSError):
                loaded = {}

        with self._lock:
            # Start with known-key defaults, overlay persisted values
            self._cache = copy.deepcopy(_KNOWN_KEYS)
            for key in loaded:
                self._cache[key] = copy.deepcopy(loaded[key])

    def _save(self) -> None:
        """Persist current cache to JSON file."""
        _SHARED_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = copy.deepcopy(self._cache)
        self._state_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    # ── CRUD ──

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value by key. Returns default if key not found."""
        with self._lock:
            if key in self._cache:
                return copy.deepcopy(self._cache[key])
            # Check known keys pattern (e.g., local_remote_control_*)
            for known in _KNOWN_KEYS:
                if known.endswith("*") and key.startswith(known[:-1]):
                    return copy.deepcopy(_KNOWN_KEYS[known])
            return copy.deepcopy(default)

    def set(self, key: str, value: Any) -> None:
        """Set a value and persist to disk. Notifies watchers."""
        old_value = None
        with self._lock:
            old_value = copy.deepcopy(self._cache.get(key))
            self._cache[key] = copy.deepcopy(value)
            watchers = list(self._watchers.get(key, [])) + list(self._watchers.get("*", []))

        self._save()
        self._notify_watchers(key, old_value, value, watchers)

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if key existed."""
        old_value = None
        existed = False
        with self._lock:
            if key in self._cache:
                old_value = copy.deepcopy(self._cache[key])
                del self._cache[key]
                existed = True
            watchers = list(self._watchers.get(key, [])) + list(self._watchers.get("*", []))

        if existed:
            self._save()
            self._notify_watchers(key, old_value, None, watchers)
        return existed

    def keys(self) -> list[str]:
        """List all keys in the store."""
        with self._lock:
            return list(self._cache.keys())

    def all(self) -> dict[str, Any]:
        """Return a deep copy of the full store."""
        with self._lock:
            return copy.deepcopy(self._cache)

    # ── Watch ──

    def watch(self, key: str, callback: Callable[[str, Any, Any], None]) -> None:
        """Register a callback for key changes.

        Callback signature: callback(key: str, old_value: Any, new_value: Any)

        Use key="*" to watch all keys.
        """
        with self._lock:
            if key not in self._watchers:
                self._watchers[key] = []
            self._watchers[key].append(callback)

    def unwatch(self, key: str, callback: Callable[[str, Any, Any], None]) -> None:
        """Remove a previously registered watch callback."""
        with self._lock:
            if key in self._watchers:
                self._watchers[key] = [cb for cb in self._watchers[key] if cb is not callback]
                if not self._watchers[key]:
                    del self._watchers[key]

    def _notify_watchers(
        self,
        key: str,
        old_value: Any,
        new_value: Any,
        watchers: list[Callable[[str, Any, Any], None]],
    ) -> None:
        for cb in watchers:
            try:
                cb(key, old_value, new_value)
            except Exception:
                pass

    # ── Convenience ──

    def get_pending_worktrees(self) -> list:
        return self.get("pending_worktrees", [])

    def add_pending_worktree(self, worktree_id: str) -> None:
        trees = self.get("pending_worktrees", [])
        trees.append(worktree_id)
        self.set("pending_worktrees", trees)

    def remove_pending_worktree(self, worktree_id: str) -> None:
        trees = self.get("pending_worktrees", [])
        trees = [t for t in trees if t != worktree_id]
        self.set("pending_worktrees", trees)


# ─── Singletons ───

shared_object_repository = SharedObjectRepository()

_shared_object_store: SharedObjectStore | None = None


def get_shared_state() -> SharedObjectStore:
    """Get the persistent SharedObjectStore singleton."""
    global _shared_object_store
    if _shared_object_store is None:
        _shared_object_store = SharedObjectStore()
    return _shared_object_store
