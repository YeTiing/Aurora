# Aurora 进程管理器 — 追踪子进程生命周期
"""管理 Agent 派生的系统进程：启动/追踪/终止"""
from __future__ import annotations
import asyncio, json, os, signal, time, uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class TrackedProcess:
    id: str
    command: str
    os_pid: int | None = None
    conversation_id: str = ""
    turn_id: str = ""
    cwd: str = "."
    status: str = "running"  # running, done, killed, error
    started_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    finished_at_ms: float = 0
    exit_code: int | None = None
    metadata: dict = field(default_factory=dict)

class ProcessManager:
    """进程管理器：跟踪 Agent 派生的所有子进程"""

    def __init__(self, state_file: str | None = None):
        self._processes: dict[str, TrackedProcess] = {}
        self._async_procs: dict[str, asyncio.subprocess.Process] = {}
        self._state_file = Path(state_file) if state_file else Path.home() / ".aurora" / "processes.json"
        self._load_state()

    def track(self, command: str, conv_id: str = "", turn_id: str = "", cwd: str = ".") -> str:
        pid = f"proc_{uuid.uuid4().hex[:8]}"
        proc = TrackedProcess(id=pid, command=command, conversation_id=conv_id, turn_id=turn_id, cwd=cwd)
        self._processes[pid] = proc
        self._save_state()
        return pid

    def attach(self, proc_id: str, os_pid: int, async_proc: asyncio.subprocess.Process | None = None):
        proc = self._processes.get(proc_id)
        if proc:
            proc.os_pid = os_pid
            if async_proc:
                self._async_procs[proc_id] = async_proc
            self._save_state()

    def finish(self, proc_id: str, exit_code: int | None = None):
        proc = self._processes.get(proc_id)
        if proc:
            proc.status = "done" if exit_code == 0 else "error"
            proc.exit_code = exit_code
            proc.finished_at_ms = time.time() * 1000
            self._async_procs.pop(proc_id, None)
            self._save_state()

    def kill(self, proc_id: str) -> bool:
        proc = self._processes.get(proc_id)
        if not proc:
            return False
        async_proc = self._async_procs.get(proc_id)
        if async_proc:
            try:
                async_proc.terminate()
            except Exception:
                pass
        elif proc.os_pid:
            try:
                os.kill(proc.os_pid, signal.SIGTERM)
            except Exception:
                pass
        proc.status = "killed"
        proc.finished_at_ms = time.time() * 1000
        self._save_state()
        return True

    def get(self, proc_id: str) -> TrackedProcess | None:
        return self._processes.get(proc_id)

    def list_all(self, status: str | None = None) -> list[TrackedProcess]:
        procs = list(self._processes.values())
        if status:
            procs = [p for p in procs if p.status == status]
        return sorted(procs, key=lambda p: -p.started_at_ms)

    def list_for_conversation(self, conv_id: str) -> list[TrackedProcess]:
        return sorted(
            [p for p in self._processes.values() if p.conversation_id == conv_id],
            key=lambda p: -p.started_at_ms
        )

    def stats(self) -> dict:
        statuses = {}
        for p in self._processes.values():
            statuses[p.status] = statuses.get(p.status, 0) + 1
        return {"total": len(self._processes), "by_status": statuses}

    def _save_state(self):
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "id": p.id, "command": p.command, "osPid": p.os_pid,
                    "conversationId": p.conversation_id, "turnId": p.turn_id,
                    "cwd": p.cwd, "status": p.status,
                    "startedAtMs": p.started_at_ms, "finishedAtMs": p.finished_at_ms,
                    "exitCode": p.exit_code,
                }
                for p in self._processes.values()
            ]
            self._state_file.write_text(json.dumps(data, indent=2, default=str), "utf-8")
        except Exception:
            pass

    def _load_state(self):
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text("utf-8"))
                for d in data:
                    pid = d.get("id", "")
                    if pid:
                        self._processes[pid] = TrackedProcess(
                            id=pid, command=d.get("command", ""), os_pid=d.get("osPid"),
                            conversation_id=d.get("conversationId", ""),
                            turn_id=d.get("turnId", ""), cwd=d.get("cwd", "."),
                            status=d.get("status", "done"),
                            started_at_ms=d.get("startedAtMs", 0),
                            finished_at_ms=d.get("finishedAtMs", 0),
                            exit_code=d.get("exitCode"),
                        )
            except Exception:
                pass

process_manager = ProcessManager()