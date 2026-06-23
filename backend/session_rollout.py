import json, os, time, uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

class RolloutWriter:
    def __init__(self, base_dir: str = "sessions"):
        self._base = Path(base_dir)
        self._file = None
        self._path = None
        self._session_id = None
        self._thread_id = None
        self._cwd = None
        self._turn_count = 0
        self._tool_call_count = 0
        self._error_count = 0
        self._token_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def open(self, session_id: str, thread_id: str = "", cwd: str = ".") -> str:
        self._session_id = session_id
        self._thread_id = thread_id or str(uuid.uuid4())[:8]
        self._cwd = cwd
        now = datetime.now()
        dir_path = self._base / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
        dir_path.mkdir(parents=True, exist_ok=True)
        ts = now.strftime("%Y%m%dT%H%M%S")
        self._path = dir_path / f"rollout-{ts}-{self._thread_id}.jsonl"
        self._file = open(str(self._path), "w", encoding="utf-8")
        self.write_event("session_meta", {
            "id": self._session_id,
            "thread_id": self._thread_id,
            "timestamp": now.isoformat(),
            "cwd": str(Path(self._cwd).resolve()),
        })
        return str(self._path)

    def write_event(self, event_type: str, payload: dict) -> None:
        if not self._file:
            return
        if event_type == "response_item":
            for item in payload.get("items", [payload]) if isinstance(payload, dict) else [payload]:
                line = json.dumps({"type": "response_item", "payload": item, "ts": time.time()}, ensure_ascii=False)
                self._file.write(line + "\n")
                if item.get("type") == "function_call":
                    self._tool_call_count += 1
                elif item.get("type") == "function_call_output" and item.get("output", "").startswith("Error:"):
                    self._error_count += 1
            return
        if event_type == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info", {})
            usage = info.get("total_token_usage", {})
            self._token_usage["input_tokens"] = usage.get("input_tokens", 0)
            self._token_usage["output_tokens"] = usage.get("output_tokens", 0)
            self._token_usage["total_tokens"] = usage.get("total_tokens", 0)
        if event_type == "turn_context":
            self._turn_count += 1
        record = {"type": event_type, "payload": payload, "ts": time.time()}
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self) -> None:
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None

    @property
    def path(self) -> Optional[str]:
        return str(self._path) if self._path else None

    @property
    def stats(self) -> dict:
        return {
            "session_id": self._session_id,
            "thread_id": self._thread_id,
            "turns": self._turn_count,
            "tool_calls": self._tool_call_count,
            "errors": self._error_count,
            "tokens": self._token_usage,
        }


class RolloutReader:
    @staticmethod
    def read(filepath: str) -> list[dict]:
        events = []
        with open(filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return events

    @staticmethod
    def replay(filepath: str):
        with open(filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

    @staticmethod
    def get_session_stats(filepath: str) -> dict:
        turns = 0
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        tool_calls = 0
        errors = 0
        session_id = ""
        thread_id = ""
        cwd = ""
        started = ""
        ended = ""
        with open(filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type", "")
                pl = ev.get("payload", {})
                ts = ev.get("ts", 0)
                if etype == "session_meta":
                    session_id = pl.get("id", "")
                    thread_id = pl.get("thread_id", "")
                    cwd = pl.get("cwd", "")
                    started = pl.get("timestamp", "")
                elif etype == "turn_context":
                    turns += 1
                elif etype == "response_item":
                    if pl.get("type") == "function_call":
                        tool_calls += 1
                    elif pl.get("type") == "function_call_output":
                        out = pl.get("output", "")
                        if out.startswith("Error:") or "error" in out.lower()[:20]:
                            errors += 1
                elif etype == "event_msg" and pl.get("type") == "token_count":
                    info = pl.get("info", {})
                    usage = info.get("total_token_usage", {})
                    total_tokens = usage.get("total_tokens", total_tokens)
                    input_tokens = usage.get("input_tokens", input_tokens)
                    output_tokens = usage.get("output_tokens", output_tokens)
                if ts and (not ended or ts > ended):
                    ended = ts
        return {
            "session_id": session_id,
            "thread_id": thread_id,
            "cwd": cwd,
            "started": started,
            "ended": datetime.fromtimestamp(ended).isoformat() if isinstance(ended, (int, float)) and ended > 0 else "",
            "turns": turns,
            "tokens": {"total": total_tokens, "input": input_tokens, "output": output_tokens},
            "tool_calls": tool_calls,
            "errors": errors,
        }

    @staticmethod
    def list_sessions(base_dir: str = "sessions") -> list[dict]:
        sessions = []
        base = Path(base_dir)
        if not base.exists():
            return sessions
        for jsonl_file in sorted(base.rglob("rollout-*.jsonl"), reverse=True):
            stats = RolloutReader.get_session_stats(str(jsonl_file))
            stats["file"] = str(jsonl_file)
            sessions.append(stats)
        return sessions


_rollout_writers: dict[str, RolloutWriter] = {}


def get_writer(session_id: str, thread_id: str = "", cwd: str = ".") -> RolloutWriter:
    if session_id not in _rollout_writers:
        w = RolloutWriter()
        w.open(session_id, thread_id, cwd)
        _rollout_writers[session_id] = w
    return _rollout_writers[session_id]


def close_writer(session_id: str) -> None:
    w = _rollout_writers.pop(session_id, None)
    if w:
        w.close()