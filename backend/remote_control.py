"""Aurora Remote Control 鈥?enrollment and connection management.

Provides RemoteControlManager for enrolling servers, managing SSH/WSL connections,
and persisting state.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

DATA_DIR = Path(__file__).parent / "data"
REMOTE_STATE_FILE = DATA_DIR / "remote_state.json"

AuthMethod = Literal["key", "password"]
ConnectionStatus = Literal["connected", "disconnected", "error"]


# ---- Data Classes ----

@dataclass
class RemoteControlEnrollment:
    """A Codex remote-control server enrollment."""
    websocket_url: str
    account_id: str = ""
    server_id: str = ""
    environment_id: str = ""
    server_name: str = ""
    remote_control_enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RemoteControlEnrollment":
        return cls(
            websocket_url=d.get("websocket_url", ""),
            account_id=d.get("account_id", ""),
            server_id=d.get("server_id", ""),
            environment_id=d.get("environment_id", ""),
            server_name=d.get("server_name", ""),
            remote_control_enabled=d.get("remote_control_enabled", True),
        )


@dataclass
class RemoteConnection:
    """An SSH-based remote connection."""
    host: str
    port: int = 22
    username: str = ""
    auth_method: AuthMethod = "key"
    status: ConnectionStatus = "disconnected"
    name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RemoteConnection":
        return cls(
            host=d.get("host", ""),
            port=d.get("port", 22),
            username=d.get("username", ""),
            auth_method=d.get("auth_method", "key"),
            status=d.get("status", "disconnected"),
            name=d.get("name", ""),
        )


@dataclass
class WSLConnection:
    """A WSL distribution connection."""
    distribution: str
    name: str = ""
    status: ConnectionStatus = "disconnected"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WSLConnection":
        return cls(
            distribution=d.get("distribution", ""),
            name=d.get("name", ""),
            status=d.get("status", "disconnected"),
        )


# ---- RemoteControlManager ----

class RemoteControlManager:
    """Manages remote-control server enrollments, SSH, and WSL connections."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if REMOTE_STATE_FILE.exists():
            try:
                return json.loads(REMOTE_STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"enrollments": {}, "ssh": {}, "wsl": {}}

    def _save_state(self) -> None:
        REMOTE_STATE_FILE.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- Enrollment Management ----

    def enroll_server(
        self,
        url: str,
        account_id: str = "",
        server_id: str = "",
        environment_id: str = "",
        server_name: str = "",
    ) -> RemoteControlEnrollment:
        if not server_id:
            import uuid
            server_id = uuid.uuid4().hex[:12]
        enrollment = RemoteControlEnrollment(
            websocket_url=url,
            account_id=account_id,
            server_id=server_id,
            environment_id=environment_id,
            server_name=server_name or server_id,
            remote_control_enabled=True,
        )
        self._state["enrollments"][server_id] = enrollment.to_dict()
        self._save_state()
        return enrollment

    def disenroll(self, server_id: str) -> bool:
        if server_id in self._state["enrollments"]:
            del self._state["enrollments"][server_id]
            self._save_state()
            return True
        return False

    def list_enrollments(self) -> list[RemoteControlEnrollment]:
        return [RemoteControlEnrollment.from_dict(v) for v in self._state["enrollments"].values()]

    def get_enrollment(self, server_id: str) -> RemoteControlEnrollment | None:
        d = self._state["enrollments"].get(server_id)
        return RemoteControlEnrollment.from_dict(d) if d else None

    # ---- SSH Connection Management ----

    def add_ssh_connection(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        auth_method: AuthMethod = "key",
        name: str = "",
    ) -> RemoteConnection:
        conn = RemoteConnection(
            host=host,
            port=port,
            username=username,
            auth_method=auth_method,
            status="disconnected",
            name=name or host,
        )
        self._state["ssh"][host] = conn.to_dict()
        self._save_state()
        return conn

    def remove_ssh_connection(self, host: str) -> bool:
        if host in self._state["ssh"]:
            del self._state["ssh"][host]
            self._save_state()
            return True
        return False

    def list_ssh_connections(self) -> list[RemoteConnection]:
        return [RemoteConnection.from_dict(v) for v in self._state["ssh"].values()]

    def get_ssh_connection(self, host: str) -> RemoteConnection | None:
        d = self._state["ssh"].get(host)
        return RemoteConnection.from_dict(d) if d else None

    # ---- WSL Connection Management ----

    def add_wsl_connection(self, distribution: str, name: str = "") -> WSLConnection:
        conn = WSLConnection(
            distribution=distribution,
            name=name or distribution,
            status="disconnected",
        )
        self._state["wsl"][name or distribution] = conn.to_dict()
        self._save_state()
        return conn

    def remove_wsl_connection(self, name: str) -> bool:
        if name in self._state["wsl"]:
            del self._state["wsl"][name]
            self._save_state()
            return True
        return False

    def list_wsl_connections(self) -> list[WSLConnection]:
        return [WSLConnection.from_dict(v) for v in self._state["wsl"].values()]

    def get_wsl_connection(self, name: str) -> WSLConnection | None:
        d = self._state["wsl"].get(name)
        return WSLConnection.from_dict(d) if d else None


# Singleton
remote_control = RemoteControlManager()
