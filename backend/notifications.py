
"""
Aurora Notification System — Desktop toast notifications.

Thread-safe NotificationManager that sends Windows toast notifications
via PowerShell balloon tip subprocess, with in-memory tracking.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_TOAST_SCRIPT = Path(__file__).resolve().parent / "scripts" / "toast.ps1"


@dataclass
class Notification:
    id: str
    title: str
    body: str = ""
    urgency: str = "normal"  # low, normal, critical
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "urgency": self.urgency,
            "created_at": self.created_at,
        }


class NotificationManager:
    """Thread-safe desktop notification manager.

    Sends Windows toast notifications via PowerShell balloon tip.
    Falls back gracefully if notification delivery fails.
    """

    def __init__(self, max_recent: int = 100):
        self._max_recent = max_recent
        self._notifications: dict[str, Notification] = {}
        self._lock = threading.Lock()

    def send(self, title: str, body: str = "", urgency: str = "normal") -> Notification:
        """Send a desktop notification. Returns the Notification object."""
        nid = uuid.uuid4().hex[:16]
        notif = Notification(id=nid, title=title, body=body, urgency=urgency)

        with self._lock:
            self._notifications[nid] = notif
            while len(self._notifications) > self._max_recent:
                oldest = min(self._notifications.values(), key=lambda n: n.created_at)
                del self._notifications[oldest.id]

        self._send_toast(notif)
        return notif

    def dismiss(self, notification_id: str) -> bool:
        """Dismiss/acknowledge a notification by ID."""
        with self._lock:
            if notification_id in self._notifications:
                del self._notifications[notification_id]
                return True
        return False

    def list_recent(self, limit: int = 50) -> list[dict]:
        """List recent notifications, newest first."""
        with self._lock:
            sorted_notifs = sorted(
                self._notifications.values(),
                key=lambda n: n.created_at,
                reverse=True,
            )
            return [n.to_dict() for n in sorted_notifs[:limit]]

    def _send_toast(self, notif: Notification) -> None:
        """Send a Windows toast notification via PowerShell balloon tip."""
        if not _TOAST_SCRIPT.exists():
            return
        try:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(_TOAST_SCRIPT),
                    "-Title", notif.title,
                    "-Body", notif.body or notif.title,
                ],
                capture_output=True,
                timeout=15,
            )
        except Exception:
            pass  # Silently fail — notifications are non-critical

    def count(self) -> int:
        with self._lock:
            return len(self._notifications)


# --- Singleton ---

_notification_manager: NotificationManager | None = None


def get_notification_manager() -> NotificationManager:
    global _notification_manager
    if _notification_manager is None:
        _notification_manager = NotificationManager()
    return _notification_manager
