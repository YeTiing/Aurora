# -*- coding: utf-8 -*-
"""LSP 传输层测试的共享假对象与工具。

单独成文件是为了让两个测试模块（协议面 / 生命周期面）各自 <300 行，
并让「哪些是 fake、哪些是真实子进程」一眼可分。

文件名以 `_` 开头，pytest 不会收集它。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

from backend.lsp.config import LspServerConfig
from backend.lsp.server_manager import LSPServerManager

REPO_ROOT = Path(__file__).resolve().parents[1]


def pid_alive(pid: int) -> bool:
    """跨平台判断 pid 是否存活（不依赖 psutil）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_dead(pid: int, timeout: float = 10.0) -> bool:
    """轮询直到 pid 消失或超时。返回是否已消失。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def feed_reader(*frames: bytes) -> asyncio.StreamReader:
    """构造一个预置了若干帧、随后 EOF 的假 StreamReader。"""
    reader = asyncio.StreamReader()
    for f in frames:
        reader.feed_data(f)
    reader.feed_eof()
    return reader


class FakeWriter:
    """收集写入字节的假 writer；不实现缓冲区，故无需 drain。"""

    def __init__(self):
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.sent.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeServer:
    """记录调用参数的假 server instance（满足 manager 的协议面）。"""

    def __init__(self, name: str = "pyright"):
        self.name = name
        self.config = LspServerConfig(
            command="pyright-langserver",
            extension_to_language={".py": "python"},
        )
        self.calls: list[tuple[str, dict]] = []
        self.result: object = []

    def is_healthy(self) -> bool:
        return True

    def is_initialized(self) -> bool:
        return True

    @property
    def capabilities(self) -> dict:
        return {}

    @property
    def pid(self) -> int:
        return 12345

    async def send_request(self, method, params):
        self.calls.append((method, params))
        return self.result


class FakeClient:
    """假 LSPClient：记录 start/initialize 的参数，不 spawn 进程。"""

    def __init__(self):
        self.started: dict | None = None
        self.init_params: dict | None = None
        self.capabilities = {"callHierarchyProvider": True}

    @property
    def is_initialized(self) -> bool:
        return True

    @property
    def pid(self) -> int:
        return 12345

    async def start(self, command, args, env=None, cwd=None):
        self.started = {"command": command, "args": args, "cwd": cwd}

    async def initialize(self, params):
        self.init_params = params
        return {"capabilities": self.capabilities}


def manager_with_fake(server: FakeServer) -> LSPServerManager:
    """构造一个绕过 initialize()（即绕过真实进程）的 manager。"""
    mgr = LSPServerManager()
    mgr._extension_map[".py"] = ["pyright"]   # noqa: SLF001 - 测试注入
    mgr._servers["pyright"] = server          # noqa: SLF001
    return mgr
