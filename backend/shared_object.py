from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


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


class SharedObjectRepository:
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
            callback(update)


shared_object_repository = SharedObjectRepository()
