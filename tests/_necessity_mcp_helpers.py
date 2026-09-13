"""MCP 测试的共享辅助 —— 单独一个文件让两个测试模块都留在 300 行预算内。

这里放三样东西：请求构造、响应断言、分帧用的假流。刻意不放任何 fixture
逻辑，避免两个模块之间出现隐式耦合。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.mcp.server import McpServer  # noqa: E402

EXPECTED_TOOLS = {"reduce_minimize", "reduce_redundancy",
                  "attribution_report", "context_lookup"}


def rpc(msg, server):
    """发一条请求，返回响应（断言不是 None）。"""
    resp = server.handle_message(msg)
    assert resp is not None, f"期望响应，实际 None: {msg}"
    return resp


def req(method, params=None, mid=1):
    m = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        m["params"] = params
    return m


def call(server, name, arguments, mid=99):
    return rpc(req("tools/call", {"name": name, "arguments": arguments}, mid), server)


def content_text(resp):
    """把 MCP 结果的 content 数组拼成文本（并校验形状）。"""
    content = resp["result"]["content"]
    assert isinstance(content, list) and content
    assert all(c["type"] == "text" for c in content)
    return "\n".join(c["text"] for c in content)


class ChunkedStream:
    """按给定切片逐次返回的假流 —— 模拟「一次 read 只有半个请求」。"""

    def __init__(self, data: bytes, cuts: list[int]):
        self._data = data
        self._cuts = list(cuts)
        self._pos = 0

    def read1(self, _n: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        n = self._cuts.pop(0) if self._cuts else len(self._data) - self._pos
        chunk = self._data[self._pos:self._pos + n]
        self._pos += n
        return chunk


def decode_lines(raw: bytes) -> list[dict]:
    return [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]


def make_tiny_git_repo(tmp_path: Path) -> Path:
    """最小 git 仓库（与 test_reduce 同构），供真实沙箱往返使用。"""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "test_mod.py").write_text(
        "from mod import VALUE\n\n\ndef test_v():\n    assert VALUE == 1\n",
        encoding="utf-8")
    for args in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                 ["git", "config", "user.name", "t"], ["git", "add", "-A"],
                 ["git", "commit", "-qm", "init"]):
        subprocess.run(args, cwd=repo, capture_output=True)
    return repo


__all__ = [
    "EXPECTED_TOOLS", "McpServer", "ROOT", "ChunkedStream", "call",
    "content_text", "decode_lines", "make_tiny_git_repo", "req", "rpc",
]
