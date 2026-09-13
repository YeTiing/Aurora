# -*- coding: utf-8 -*-
"""子进程清理兜底 —— atexit + signal，防止 LSP server 变孤儿进程。

移植自 Necessity core/index/process_cleanup.py：Aurora 原先只在
`stop()` 里 terminate/kill，主进程被 Ctrl+C 或硬杀时 stop() 不会
执行，pyright 留在内存里堆积。覆盖 SIGINT/SIGTERM/SIGBREAK，
并在 kill 后链式调用原 handler（否则 Ctrl+C 会被吞掉）。

解决 INDEX.md §2.2 缺陷③。Aurora 只在 `stop()` 里 terminate/kill，
可正常退出能清理；**主进程被 Ctrl+C 或硬杀时 `stop()` 不执行**，
pyright 就留下，反复运行会堆积。

本模块把这些兜底独立出来，因为清理逻辑必须满足两个约束：
  1. 在 signal / atexit 上下文里同步执行（那里没有事件循环，不能 await）
  2. 必须能杀**整棵进程树**，不是直接子进程

Windows 上 `pyright-langserver` 是 npm 生成的 `.cmd` shim，真正跑的是它
拉起的 node 孙子进程。只 `proc.kill()` 直接子进程，node 会活下来——
这正是「Aurora 有 stop() 却仍可能孤儿」的根因。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import subprocess
import threading
from typing import Any, Callable

log = logging.getLogger("necessity.index.process_cleanup")

__all__ = ["kill_process_tree", "ProcessCleanup", "PollingReaper"]


def kill_process_tree(pid: int) -> None:
    """同步、尽力而为地杀掉 pid 及其全部子孙。永不抛异常。

    必须在 signal 上下文可用，所以全部用同步 API。
    """
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, check=False,
            )
        else:
            # start_new_session=True 时 pgid == pid，整组清掉
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return  # 已死 / 不是我们的子进程
    except Exception as e:
        log.debug(f"kill_process_tree({pid}) failed: {e}")


class ProcessCleanup:
    """为某个子进程注册/注销退出兜底。

    `get_pid` 是回调而非固定 pid：进程可能重启，pid 会变，兜底必须
    每次都拿到**当前**的 pid。
    """

    def __init__(self, name: str, get_pid: Callable[[], int | None]):
        self._name = name
        self._get_pid = get_pid
        self._registered = False
        self._prev_handlers: dict[int, Any] = {}

    def register(self) -> None:
        """注册 atexit；若在主线程则同时接管 SIGINT/SIGTERM。幂等。"""
        if self._registered:
            return
        atexit.register(self.emergency_kill)
        self._registered = True

        if threading.current_thread() is not threading.main_thread():
            # signal.signal 仅限主线程；worker 线程里只能靠 atexit。
            return
        for sig in self._signals():
            try:
                self._prev_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass

    @staticmethod
    def _signals() -> tuple[int, ...]:
        """要接管的信号。Windows 额外接 SIGBREAK。

        Ctrl+Break 在 Windows 上会触发 SIGBREAK 而非 SIGINT，不去接它
        就等于漏掉一半的 Ctrl 类中断（Aurora 完全没接，缺陷③的一半在这里）。
        """
        sigs = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            sigs.append(signal.SIGBREAK)
        return tuple(sigs)

    def unregister(self) -> None:
        """正常 stop() 后注销，避免兜底被反复触发。幂等。"""
        if self._registered:
            try:
                atexit.unregister(self.emergency_kill)
            except Exception:
                pass
            self._registered = False
        if threading.current_thread() is not threading.main_thread():
            return
        for sig, prev in self._prev_handlers.items():
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError, TypeError):
                pass
        self._prev_handlers.clear()

    def emergency_kill(self) -> None:
        """同步强杀当前子进程。供 atexit / signal 调用。"""
        pid = self._get_pid()
        if pid:
            log.warning(f"清理兜底触发：杀掉 LSP server '{self._name}' (pid={pid})")
            kill_process_tree(pid)

    def _on_signal(self, signum: int, frame: Any) -> None:
        """杀子进程后**恢复原行为**——绝不吞掉用户的 Ctrl+C。"""
        self.emergency_kill()
        prev = self._prev_handlers.get(signum)
        if callable(prev):
            prev(signum, frame)
        else:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)


class PollingReaper:
    """跨平台「强杀父进程 → 立即同步回收」的补齐。

    实测发现（Windows）：`taskkill /F /T` 发出后子进程不一定在 kill 返回
    时已被系统回收，`proc.returncode` 可能仍是 None，于是 stop() 里的
    `await proc.wait()` 会再多等一个 SHUTDOWN_TIMEOUT。这不是正确性问题，
    但让关停慢 5s；快速轮询 returncode 可把这个窗口压到毫秒级。
    """

    ASAP_LIMIT = 50_000  # 5s（0.1ms × 50000）

    @staticmethod
    async def reap(process: Any | None) -> None:
        """轮询 `process.returncode` 直到非 None 或超时。永不抛异常。"""
        if process is None:
            return
        for _ in range(PollingReaper.ASAP_LIMIT):
            if process.returncode is not None:
                return
            await asyncio.sleep(0.0001)
