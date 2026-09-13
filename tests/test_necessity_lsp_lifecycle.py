# -*- coding: utf-8 -*-
"""LSP 进程生命周期测试 —— 缺陷③（孤儿进程）的真实验证。

这里用**真实子进程**（`sys.executable`，不是 pyright），因为孤儿清理的
唯一可信验证就是「进程真的死了没有」。全部离线可跑。
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys

import pytest

from backend.lsp.client import create_lsp_client
from backend.lsp.process_cleanup import ProcessCleanup, kill_process_tree
from tests._necessity_lsp_fakes import REPO_ROOT, pid_alive, wait_dead

SLEEP_SCRIPT = ["-c", "import time; time.sleep(300)"]


# ── 缺陷③：孤儿进程清理 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_actually_kills_child_process():
    """stop() 之后子进程必须真的死了，而不只是被标记。"""
    client = create_lsp_client("sleepy")
    await client.start(sys.executable, SLEEP_SCRIPT)
    pid = client.pid
    assert pid and pid_alive(pid)

    await client.stop()
    assert wait_dead(pid), f"stop() 后 pid {pid} 仍然存活"


@pytest.mark.asyncio
async def test_emergency_kill_kills_child():
    """atexit/signal 实际调用的 emergency_kill 本体必须能杀进程。"""
    client = create_lsp_client("sleepy")
    await client.start(sys.executable, SLEEP_SCRIPT)
    pid = client.pid
    try:
        assert pid_alive(pid)
        client._cleanup.emergency_kill()  # noqa: SLF001
        assert wait_dead(pid), f"emergency_kill() 后 pid {pid} 仍然存活"
    finally:
        client._cleanup.unregister()  # noqa: SLF001


@pytest.mark.asyncio
async def test_atexit_reaps_orphan_end_to_end(tmp_path):
    """真正验证 atexit：子解释器**不调 stop()** 直接退出，孙进程必须被兜底杀掉。

    这不是「我加了 atexit」的断言，而是跑一个独立的 Python 进程，
    让它正常退出、依赖 atexit 触发，然后验证它拉起的孙进程已消失。
    """
    helper = tmp_path / "orphan_helper.py"
    helper.write_text(
        "import asyncio, sys\n"
        "from backend.lsp.client import create_lsp_client\n"
        "async def main():\n"
        "    c = create_lsp_client('orphan')\n"
        "    await c.start(sys.executable, ['-c', 'import time; time.sleep(300)'])\n"
        "    print(c.pid, flush=True)\n"
        "asyncio.run(main())\n"
        "# 故意不调用 stop()：只能靠 atexit 兜底\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, str(helper)],
        capture_output=True, text=True, timeout=90, env=env, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"helper 异常退出: {proc.stderr}"
    grandchild_pid = int(proc.stdout.strip().splitlines()[-1])
    assert wait_dead(grandchild_pid), (
        f"atexit 未清理孙进程 pid={grandchild_pid}；helper stderr={proc.stderr}"
    )


@pytest.mark.asyncio
async def test_signal_handler_installed_and_kills_then_chains():
    """SIGINT 处理器必须：① 已安装 ② 杀子进程 ③ 不吞掉原 handler（会链式调用）。

    ③ 尤其重要：兜底不能把用户的 Ctrl+C 变成「卡住不退」。这里用一个
    记录调用的假原 handler 验证链式行为，不真的给自己发信号。
    """
    client = create_lsp_client("sig")
    await client.start(sys.executable, SLEEP_SCRIPT)
    pid = client.pid
    cleanup = client._cleanup  # noqa: SLF001
    try:
        inst = signal.getsignal(signal.SIGINT)
        assert callable(inst), "SIGINT 处理器未被接管"

        chained: list[int] = []

        def _previous(signum, frame):
            chained.append(signum)

        cleanup._prev_handlers[signal.SIGINT] = _previous  # noqa: SLF001
        cleanup._on_signal(signal.SIGINT, None)  # noqa: SLF001

        assert wait_dead(pid), f"信号处理后 pid {pid} 仍存活"
        assert chained == [signal.SIGINT], "未链式调用原 handler（会吞掉 Ctrl+C）"
    finally:
        cleanup.unregister()  # noqa: SLF001


def test_cleanup_registers_sigbreak_on_windows():
    """Windows 必须同时接管 SIGBREAK，否则 Ctrl+Break 会漏杀。"""
    if not hasattr(signal, "SIGBREAK"):
        pytest.skip("非 Windows，无 SIGBREAK")
    assert signal.SIGBREAK in ProcessCleanup._signals()  # noqa: SLF001


@pytest.mark.skipif(os.name != "nt", reason="进程树清理是 Windows 专属路径")
def test_kill_process_tree_kills_grandchild(tmp_path):
    """Windows 的 taskkill /T 必须能杀掉孙子进程（.cmd shim 场景）。

    用 helper 脚本而非 `cmd /c` 内联引号：后者在 bash→cmd 之间会被转义
    规则吃掉引号，测试本身不稳定。
    """
    helper = tmp_path / "spawn_grandchild.py"
    helper.write_text(
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(helper)],
        stdout=subprocess.PIPE, text=True, creationflags=0x08000000,
    )
    try:
        line = proc.stdout.readline().strip()
        assert line.isdigit(), f"拿不到孙进程 pid: {line!r}"
        grandchild_pid = int(line)
        assert pid_alive(grandchild_pid)

        kill_process_tree(proc.pid)
        assert wait_dead(grandchild_pid), f"树杀后孙进程 {grandchild_pid} 仍存活"
    finally:
        proc.kill()
