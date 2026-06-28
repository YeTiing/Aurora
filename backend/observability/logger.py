# Structured Logger
from __future__ import annotations
import json, time, threading, os, sys, atexit
from enum import IntEnum
from pathlib import Path


class LogLevel(IntEnum):
    TRACE = 0
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40
    FATAL = 50

    @property
    def label(self) -> str:
        return self.name


class Logger:
    _instances: dict[str, "Logger"] = {}
    _lock = threading.Lock()
    _global_level: LogLevel = LogLevel.INFO

    def __init__(self, name: str, log_file: str = ""):
        self.name = name
        self._file = None
        self._file_lock = threading.Lock()
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            self._file = open(log_file, "a", encoding="utf-8")
            Logger._all_files.append(self._file)

    _all_files: list = []  # track all open file handles for cleanup

    @classmethod
    def _cleanup_all(cls):
        for fh in cls._all_files:
            try:
                fh.close()
            except Exception:
                pass
        cls._all_files.clear()

    @classmethod
    def get(cls, name: str = "aurora") -> "Logger":
        with cls._lock:
            if name not in cls._instances:
                cls._instances[name] = cls(name)
            return cls._instances[name]

    @classmethod
    def set_level(cls, level: LogLevel):
        cls._global_level = level

    def log(self, level: LogLevel, message: str, **kwargs):
        if level < self._global_level:
            return
        entry = {
            "timestamp": time.time(), "level": level.label,
            "logger": self.name, "message": message,
            "pid": os.getpid(), "thread": threading.current_thread().name,
            **kwargs,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        color_map = {"TRACE": 90, "DEBUG": 36, "INFO": 32, "WARN": 33, "ERROR": 31, "FATAL": 35}
        c = color_map.get(level.label, 0)
        ts = time.strftime("%H:%M:%S", time.localtime(entry["timestamp"]))
        if c:
            print(f"\033[{c}m{ts} [{level.label:<5}] {self.name:<12} {message}\033[0m", file=sys.stderr)
        else:
            print(f"{ts} [{level.label:<5}] {self.name:<12} {message}", file=sys.stderr)
        if self._file:
            with self._file_lock:
                self._file.write(line + "\n")
                self._file.flush()

    def trace(self, msg, **kw): self.log(LogLevel.TRACE, msg, **kw)
    def debug(self, msg, **kw): self.log(LogLevel.DEBUG, msg, **kw)
    def info(self, msg, **kw): self.log(LogLevel.INFO, msg, **kw)
    def warn(self, msg, **kw): self.log(LogLevel.WARN, msg, **kw)
    def error(self, msg, **kw): self.log(LogLevel.ERROR, msg, **kw)
    def fatal(self, msg, **kw): self.log(LogLevel.FATAL, msg, **kw)

    def close(self):
        if self._file:
            self._file.close()
            self._file = None


atexit.register(Logger._cleanup_all)
log = Logger.get("aurora")