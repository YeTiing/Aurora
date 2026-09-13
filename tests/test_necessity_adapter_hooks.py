"""适配层测试 —— 契约 1（钩子故障不得阻塞任务）的强制验证。

这一层存在的核心理由就是把「任何异常都放行」变成**结构保证**，
而不是依赖每个 core 实现自觉兜异常。所以测试的重点是：
让钩子以各种方式爆炸，断言宿主永远拿到安全的默认值。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity import adapter as H  # noqa: E402
from backend.necessity.hooks import (  # noqa: E402
    Decision,
    NullHooks,
    ReadResult,
    TaskResult,
    ToolCall,
    ToolResult,
)

_T = TaskResult(task_id="t", ok=True)


@pytest.fixture(autouse=True)
def _reset_hooks():
    """每个用例前后都恢复 NullHooks，避免用例间通过模块级状态互相污染。"""
    H.set_hooks(None)
    yield
    H.set_hooks(None)


class Boom:
    """所有方法都抛异常的钩子实现。"""

    def on_task_start(self, task): raise RuntimeError("boom")
    def on_turn_end(self, turn): raise RuntimeError("boom")
    def on_task_end(self, result): raise RuntimeError("boom")
    def before_tool(self, call): raise RuntimeError("boom")
    def after_tool(self, call, result): raise RuntimeError("boom")
    def read_file(self, path, opts): raise RuntimeError("boom")
    def after_write(self, path, writer): raise RuntimeError("boom")
    def before_compaction(self, messages): raise RuntimeError("boom")
    def after_compaction(self, summary): raise RuntimeError("boom")
    def scan_workspace(self): raise RuntimeError("boom")


class WrongType:
    """返回错误类型的钩子 —— 宿主不能因此拿到非法对象。"""

    def before_tool(self, call): return "not a Decision"
    def after_compaction(self, summary): return 12345
    def scan_workspace(self): return {"not": "a list"}
    def on_task_end(self, result): return "not a dict"


# ── 默认状态 ──────────────────────────────────────────────────────

def test_default_is_null_hooks_and_never_blocks():
    """未启用任何能力时，宿主行为必须完全不变。"""
    d = H.before_tool("shell_command", {"command": "rm -rf /"}, turn=1)
    assert d.action == "allow"
    assert d.reason == ""
    assert H.read_file("x.py", {}) is None, "默认必须返回 None 表示『本层不管』"
    assert H.scan_workspace() == []
    assert H.after_compaction("s") == "s"


def test_get_hooks_default_type():
    assert isinstance(H.get_hooks(), NullHooks)


# ── 契约 1：任何故障都放行 ────────────────────────────────────────

def test_exception_in_every_hook_is_swallowed():
    """逐钩子注入异常，断言全部退回安全默认值，且没有任何一个向外抛。"""
    H.set_hooks(Boom())

    H.on_task_start({"x": 1})                       # 不抛
    H.on_turn_end(1)                                # 不抛
    assert H.on_task_end(_T) == {} # 退回空 dict

    assert H.before_tool("t", {}, 1).action == "allow"
    H.after_tool("t", {"success": True}, 1)         # 不抛
    assert H.read_file("p", {}) is None             # 退回「本层不管」
    H.after_write("p", "agent")                     # 不抛
    H.before_compaction([])                         # 不抛
    assert H.after_compaction("original") == "original", "异常时必须保留原 summary"
    assert H.scan_workspace() == []


def test_error_counter_records_failures():
    """故障次数要可观测 —— 否则『机制没被用起来』无法排查。"""
    H.set_hooks(Boom())
    before = H.hook_stats()["errors"]
    H.before_tool("t", {}, 1)
    H.after_tool("t", {"success": True}, 1)
    assert H.hook_stats()["errors"] >= before + 2


def test_wrong_return_types_are_normalized():
    """返回类型不对时不能把非法对象泄漏给宿主。"""
    H.set_hooks(WrongType())

    d = H.before_tool("t", {}, 1)
    assert isinstance(d, Decision), "非 Decision 返回值必须被替换为安全默认"
    assert d.action == "allow"

    assert H.after_compaction("s") == "s"
    assert H.scan_workspace() == []
    assert H.on_task_end(_T) == {}


# ── 正常路径 ──────────────────────────────────────────────────────

class Recording:
    def __init__(self):
        self.calls = []

    def before_tool(self, call: ToolCall) -> Decision:
        self.calls.append(("before_tool", call.name))
        if call.name == "shell_command":
            return Decision(action="block", reason="约束违反")
        return Decision()

    def after_tool(self, call: ToolCall, result: ToolResult) -> None:
        self.calls.append(("after_tool", result.ok))

    def read_file(self, path: str, opts: dict):
        if path == "cached.py":
            return ReadResult(content="INDEX", mode="index", path=path)
        return None

    def after_compaction(self, summary: str) -> str:
        return summary + "\n[file_state]"


def test_block_decision_reaches_host_with_reason():
    H.set_hooks(Recording())
    d = H.before_tool("shell_command", {"command": "x"}, 1)
    assert d.action == "block"
    assert d.reason, "block 必须带 reason —— 它是 Agent 纠正的唯一线索"


def test_allow_by_default_for_other_tools():
    H.set_hooks(Recording())
    assert H.before_tool("code_search", {}, 1).action == "allow"


def test_read_file_none_means_host_reads_itself():
    """返回 None 是「本层不管」的契约 —— 宿主据此走原逻辑。"""
    H.set_hooks(Recording())
    assert H.read_file("not_cached.py", {}) is None
    r = H.read_file("cached.py", {})
    assert r is not None and r.mode == "index"


def test_after_tool_normalizes_host_result_dict():
    """Aurora 的工具返回是 dict，必须被归一化后再进 core。"""
    H.set_hooks(Recording())
    H.after_tool("t", {"success": True, "output": "ok", "error": None}, 1)
    assert ("after_tool", True) in H.get_hooks().calls


def test_after_tool_handles_error_none():
    """Aurora 成功结果的 error 字段常是 None —— 不能因此崩。"""
    H.set_hooks(Recording())
    H.after_tool("t", {"success": True, "output": "ok", "error": None}, 1)
    H.after_tool("t", {"success": False, "output": "", "error": "boom"}, 1)


def test_after_compaction_enhancement_applies():
    H.set_hooks(Recording())
    assert H.after_compaction("S") == "S\n[file_state]"


def test_calls_counter_is_observable():
    H.set_hooks(Recording())
    before = H.hook_stats()["calls"]
    H.before_tool("t", {}, 1)
    H.after_tool("t", {"success": True}, 1)
    assert H.hook_stats()["calls"] == before + 2


# ── 契约：mount 默认关闭 ──────────────────────────────────────────

def test_install_is_noop_by_default(monkeypatch):
    """未显式启用时必须不挂载任何实现（I1 空操作挂载）。"""
    from backend.necessity import adapter as mount

    monkeypatch.delenv("NECESSITY_ENABLED", raising=False)
    assert mount.is_enabled() is False
    assert mount.install({}) is False
    assert isinstance(H.get_hooks(), NullHooks)


def test_mount_points_use_symbol_names_not_line_numbers():
    """挂载点清单不得依赖行号 —— 宿主演进会让它漂移。"""
    from backend.necessity import adapter as mount

    for name, spec in mount.MOUNT_POINTS.items():
        assert not any(ch.isdigit() for ch in spec.split("(")[0]), \
            f"{name} 的挂载点描述里出现了行号：{spec}"
