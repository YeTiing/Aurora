"""Aurora Remote Control — enrollment and connection management.

Provides RemoteControlManager for enrolling servers, managing SSH/WSL connections,
and persisting state.
"""
from __future__ import annotations

import json
import os
import asyncio
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

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
        self._ssh_sessions: dict[str, Any] = {}
        self._wsl_processes: dict[str, asyncio.subprocess.Process] = {}

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

    # ---- SSH Real Connection Logic ----

    async def connect_ssh(self, host: str) -> bool:
        """Connect to an SSH host using asyncssh (preferred) or subprocess fallback."""
        conn = self.get_ssh_connection(host)

        try:
            from asyncssh import connect as asyncssh_connect
            username = conn.username if conn and conn.username else None
            port = conn.port if conn else 22
            session = await asyncssh_connect(
                host, port=port, username=username,
                known_hosts=None,
            )
            self._ssh_sessions[host] = session
            if conn:
                conn.status = "connected"
                self._state["ssh"][host] = conn.to_dict()
                self._save_state()
            logger.info("SSH connected to %s via asyncssh", host)
            return True
        except ImportError:
            logger.debug("asyncssh not available for %s, using subprocess fallback", host)
        except Exception as e:
            logger.warning("asyncssh connect to %s failed: %s, falling back", host, e)

        # Subprocess fallback — connection tested per-command
        if conn:
            conn.status = "connected"
            self._state["ssh"][host] = conn.to_dict()
            self._save_state()
        self._ssh_sessions[host] = "subprocess"
        logger.info("SSH %s marked connected (subprocess fallback)", host)
        return True

    async def disconnect_ssh(self, host: str) -> bool:
        """Disconnect from an SSH host."""
        session = self._ssh_sessions.pop(host, None)
        if session is not None:
            try:
                if hasattr(session, "close"):
                    session.close()
                    await session.wait_closed()
            except Exception as e:
                logger.warning("Error closing SSH session for %s: %s", host, e)

        conn = self.get_ssh_connection(host)
        if conn:
            conn.status = "disconnected"
            self._state["ssh"][host] = conn.to_dict()
            self._save_state()
        logger.info("SSH disconnected from %s", host)
        return True

    async def run_ssh_command(self, host: str, command: str, timeout: float = 30) -> dict:
        """Run a command on a connected SSH host. Returns {stdout, stderr, exit_code}."""
        session = self._ssh_sessions.get(host)

        # asyncssh path
        if session is not None and hasattr(session, "run"):
            try:
                result = await asyncio.wait_for(session.run(command), timeout=timeout)
                return {
                    "stdout": result.stdout or "",
                    "stderr": result.stderr or "",
                    "exit_code": result.exit_status or result.returncode or 0,
                }
            except asyncio.TimeoutError:
                return {"stdout": "", "stderr": "Command timed out", "exit_code": -1}
            except Exception as e:
                return {"stdout": "", "stderr": str(e), "exit_code": -1}

        # Subprocess fallback
        conn = self.get_ssh_connection(host)
        username = conn.username if conn else ""
        ssh_host = f"{username}@{host}" if username else host

        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                ssh_host, command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
                "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
                "exit_code": proc.returncode or 0,
            }
        except asyncio.TimeoutError:
            return {"stdout": "", "stderr": "Command timed out", "exit_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    # ---- WSL Real Connection Logic ----

    async def connect_wsl(self, distribution: str) -> bool:
        """Connect to a WSL distribution via wsl.exe subprocess probe."""
        conn = self.get_wsl_connection(distribution)

        try:
            proc = await asyncio.create_subprocess_exec(
                "wsl.exe", "-d", distribution, "echo", "Aurora WSL connected",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                logger.error("WSL connect to %s failed: %s", distribution, stderr.decode())
                return False

            if conn:
                conn.status = "connected"
                self._state["wsl"][distribution] = conn.to_dict()
                self._save_state()
            self._wsl_processes[distribution] = proc
            logger.info("WSL connected to %s", distribution)
            return True
        except Exception as e:
            logger.error("WSL connect to %s failed: %s", distribution, e)
            return False

    async def run_wsl_command(self, distribution: str, command: str, timeout: float = 30) -> dict:
        """Run a command inside a WSL distribution."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "wsl.exe", "-d", distribution, "--", "bash", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
                "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
                "exit_code": proc.returncode or 0,
            }
        except asyncio.TimeoutError:
            return {"stdout": "", "stderr": "Command timed out", "exit_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}


# Singleton
remote_control = RemoteControlManager()
