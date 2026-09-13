# -*- coding: utf-8 -*-
"""P1 修复回归测试：B1 / B10 / B11 / B12 / A2。

每个用例都构造真实数据结构驱动被测函数，断言修复后的行为，
而不是仅做 smoke test。
"""
from __future__ import annotations

import asyncio
import builtins
import json
import sys
import time

import pytest

from backend.agent.llm_client import MockLLMClient
from backend.agent.llm_providers import LLMResponse
from backend.agent.nodes import executor_node, tool_select_node
from backend.agent.state import AgentState, Message, ToolInvocation


@pytest.fixture(autouse=True)
def _stub_post_edit_hooks(monkeypatch):
    """屏蔽真实的 LSP / 安全扫描后处理。

    它们会对整个工作区做昂贵扫描，既拖慢测试又与被测的并发逻辑无关；
    后处理本身由 test_a2_post_edit_hooks_run_for_every_result 单独验证。
    """
    import backend.agent.nodes as nodes

    async def _lsp(*args, **kwargs):
        return ""

    async def _sec(*args, **kwargs):
        return ""

    monkeypatch.setattr(nodes, "post_file_edit_hook", _lsp)
    monkeypatch.setattr(nodes, "post_edit_security_hook", _sec)


# ══════════════════════════════════════════════════════════════
# B1: 并行工具调用 -> 恰好一条 assistant 消息，携带全部 tool_calls
# ══════════════════════════════════════════════════════════════

class ThreeToolCallLLM(MockLLMClient):
    async def chat(self, messages, tools=None, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[
                {"id": "call_a", "type": "function",
                 "function": {"name": "list_files", "arguments": json.dumps({"path": "."})}},
                {"id": "call_b", "type": "function",
                 "function": {"name": "code_search", "arguments": json.dumps({"query": "x"})}},
                {"id": "call_c", "type": "function",
                 "function": {"name": "file_rw", "arguments": json.dumps({"operation": "read", "path": "a.py"})}},
            ],
            finish_reason="tool_calls",
            model="mock",
        )


class NoIdThreeToolCallLLM(MockLLMClient):
    """故意不带 id，验证回退 id 每调用唯一。"""

    async def chat(self, messages, tools=None, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[
                {"function": {"name": "list_files", "arguments": "{}"}},
                {"function": {"name": "code_search", "arguments": "{}"}},
                {"function": {"name": "file_rw", "arguments": "{}"}},
            ],
            finish_reason="tool_calls",
            model="mock",
        )


@pytest.mark.asyncio
async def test_b1_parallel_tool_calls_single_assistant_message():
    state = AgentState(session_id="b1")
    state.add_message(Message.user("do three things"))

    await tool_select_node(state, ThreeToolCallLLM(), [])

    assistants = [m for m in state.messages if m.role == "assistant"]
    assert len(assistants) == 1, f"expected 1 assistant message, got {len(assistants)}"
    # 唯一的 assistant 必须携带全部 3 个 tool_calls
    assert len(assistants[0].tool_calls) == 3
    assert len(state.tool_invocations) == 3


@pytest.mark.asyncio
async def test_b1_tool_call_ids_are_distinct():
    state = AgentState(session_id="b1-ids")
    state.add_message(Message.user("do three things"))

    await tool_select_node(state, ThreeToolCallLLM(), [])

    ids = [inv.id for inv in state.tool_invocations]
    assert ids == ["call_a", "call_b", "call_c"]
    # 与 assistant 消息里携带的 id 一一对应
    msg_ids = [tc["id"] for tc in state.messages[-1].tool_calls]
    assert msg_ids == ids


@pytest.mark.asyncio
async def test_b1_missing_ids_get_unique_fallback():
    state = AgentState(session_id="b1-fallback")
    state.add_message(Message.user("do three things"))

    await tool_select_node(state, NoIdThreeToolCallLLM(), [])

    ids = [inv.id for inv in state.tool_invocations]
    assert len(ids) == 3
    assert len(set(ids)) == 3, f"fallback ids must be unique, got {ids}"


@pytest.mark.asyncio
async def test_b1_history_is_legal_after_execution():
    """3 个并行调用执行后：1 条 assistant(tool_calls=3) + 3 条 tool 结果，
    且每个 tool_call_id 都能在 assistant 消息里找到配对。"""
    state = AgentState(session_id="b1-history")
    state.add_message(Message.user("do three things"))
    await tool_select_node(state, ThreeToolCallLLM(), [])

    calls = {tc["id"] for tc in state.messages[-1].tool_calls}

    async def handler(name, args, ws):
        return {"success": True, "output": f"{name} ok"}

    await executor_node(state, handler, ".")

    assistant_count = sum(1 for m in state.messages if m.role == "assistant")
    tool_msgs = [m for m in state.messages if m.role == "tool"]
    assert assistant_count == 1
    assert len(tool_msgs) == 3
    assert {m.tool_call_id for m in tool_msgs} == calls


# ══════════════════════════════════════════════════════════════
# B10: 导入零副作用 + 断网降级
# ══════════════════════════════════════════════════════════════

def test_b10_import_does_not_construct_encoder(monkeypatch):
    """模块导入 / 构造 TokenCounter 都不得解析编码器（否则即联网）。"""
    import backend.context.token_counter as tc

    # 让任何编码器解析立刻 SSLError，证明构造期不碰它
    import requests

    def _boom(*a, **k):
        raise requests.exceptions.SSLError("offline")

    monkeypatch.setattr(tc, "_encoder_lookup", _boom)

    c = tc.TokenCounter()
    assert c._encoder is None  # 构造后仍未解析


def test_b10_count_falls_back_offline(monkeypatch):
    import requests
    import backend.context.token_counter as tc

    def _boom(*a, **k):
        raise requests.exceptions.SSLError("offline")

    monkeypatch.setattr(tc, "_encoder_lookup", _boom)
    # 清空缓存，保证走降级分支
    monkeypatch.setattr(tc, "_ENCODER_CACHE", {})

    c = tc.TokenCounter()
    n = c.count("a" * 400)
    assert n == 100  # 400 // 4
    # 其余 API 仍可用
    assert c.count_messages([{"role": "user", "content": "abcd"}]) >= 4
    assert c.count_tool_schemas([{"type": "function"}]) >= 0
    c.change_model("gpt-4")
    assert c.count("abcdefgh") == 2
    assert c.count("") == 0


def test_b10_module_imports_without_tiktoken(monkeypatch):
    """tiktoken 不可用时模块也必须能干净导入并工作。

    用独立的模块名从源码重新加载，避免污染真实模块缓存。
    """
    import importlib.util
    import os
    import builtins
    import backend.context.token_counter as real_tc

    path = os.path.join(os.path.dirname(real_tc.__file__), "token_counter.py")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("no tiktoken")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    spec = importlib.util.spec_from_file_location("_tc_no_tiktoken", path)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__name__"] = "_tc_no_tiktoken"
    spec.loader.exec_module(mod)  # 导入本身不得抛异常

    assert mod.tiktoken is None
    c = mod.TokenCounter()
    assert c.count("abcdefgh") == 2  # 字符降级估计


def test_b10_public_counter_name_still_works():
    """公开名字 counter 保持可用（惰性代理），且与 get_counter 同一实例。"""
    import backend.context.token_counter as tc
    from backend.context.token_counter import counter, get_counter

    assert counter is not get_counter()  # 代理本身
    assert counter.count("hello") > 0
    assert counter.model == get_counter().model
    assert tc.get_counter() is get_counter()  # 惰性单例


# ══════════════════════════════════════════════════════════════
# B11: 压缩保留 system 消息
# ══════════════════════════════════════════════════════════════

def test_b11_summarize_preserves_system_message():
    from backend.context.collapse import ContextCollapser, CollapseConfig

    c = ContextCollapser(CollapseConfig(
        max_messages=10, summary_turn_threshold=3, auto_compact=True,
    ))
    msgs = []
    for i in range(10):
        msgs.append({"role": "user", "content": f"user {i}"})
        msgs.append({"role": "assistant", "content": f"assistant {i}"})
    msgs.append({"role": "system", "content": "CONSTRAINT: do not change DB schema"})

    summary = c._summarize(msgs)
    assert "CONSTRAINT: do not change DB schema" in summary
    assert "System:" in summary


def test_b11_steering_constraint_survives_collapse():
    """完整链路：system 引导 + 长历史 -> collapse 后约束仍在 summary。"""
    from backend.context.collapse import ContextCollapser, CollapseConfig

    c = ContextCollapser(CollapseConfig(
        max_messages=10, summary_turn_threshold=3, auto_compact=True,
    ))
    # system 约束放在会被折叠的「旧消息」区间。历史长度控制在 30 行内，
    # 因为 _summarize 保留 lines[-30:]（既有截断行为，不在本次修复范围）。
    msgs = [{"role": "system", "content": "不要修改数据库 schema"}]
    msgs.append({"role": "user", "content": "start"})
    for i in range(16):
        msgs.append({"role": "user", "content": f"turn {i}"})
        msgs.append({"role": "assistant", "content": f"reply {i}"})

    collapsed, summary = c.collapse(msgs, keep_last=10)
    assert "不要修改数据库 schema" in summary
    # 压缩确实发生
    assert len(collapsed) < len(msgs)


def test_b11_unknown_role_not_dropped():
    from backend.context.collapse import ContextCollapser

    c = ContextCollapser()
    summary = c._summarize([
        {"role": "developer", "content": "custom role content"},
        {"role": "user", "content": "hi"},
    ])
    assert "custom role content" in summary


# ══════════════════════════════════════════════════════════════
# B12: compact_async 真正可用
# ══════════════════════════════════════════════════════════════

class SummarizingLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return LLMResponse(content="HANDOFF SUMMARY: did A and B; next do C.", model="mock")


@pytest.mark.asyncio
async def test_b12_compact_async_produces_summary_and_shrinks():
    from backend.context.context_manager import ContextManager

    llm = SummarizingLLM()
    cm = ContextManager()
    cm.set_messages([
        {"role": "user", "content": f"msg {i}"} for i in range(12)
    ])
    before = len(cm.messages)

    removed = await cm.compact_async(llm)

    assert removed > 0
    assert len(cm.messages) < before
    assert llm.calls == 1, "must actually call the LLM"
    # 第一条是 LLM 生成的摘要
    assert cm.messages[0]["role"] == "system"
    assert "HANDOFF SUMMARY" in cm.messages[0]["content"]


@pytest.mark.asyncio
async def test_b12_compact_async_returns_zero_when_short():
    from backend.context.context_manager import ContextManager

    cm = ContextManager()
    cm.set_messages([{"role": "user", "content": "short"}])
    assert await cm.compact_async(SummarizingLLM()) == 0


@pytest.mark.asyncio
async def test_b12_needs_compaction_uses_injected_messages():
    from backend.context.context_manager import ContextManager

    cm = ContextManager()
    # max_tokens=100, threshold 0.85 -> 超过 85 token 即需要压缩
    cm.set_max_tokens(100)
    cm.set_messages([{"role": "user", "content": "x" * 5000}])
    assert cm.needs_compaction() is True

    cm.set_max_tokens(10_000_000)
    assert cm.needs_compaction() is False


@pytest.mark.asyncio
async def test_b12_maybe_compact_context_wires_into_agent_loop():
    """验证 graph.py 可用的单行接线入口：压短历史且保留 tool 配对字段。"""
    from backend.agent.nodes import maybe_compact_context
    from backend.agent.state import AgentState, Message

    state = AgentState(session_id="b12-wire")
    for i in range(12):
        state.add_message(Message.user(f"msg {i}"))
    before = len(state.messages)

    changed = await maybe_compact_context(state, SummarizingLLM(), max_tokens=100)

    assert changed is True
    assert len(state.messages) < before
    assert state.messages[0].role == "system"
    assert "HANDOFF SUMMARY" in state.messages[0].content


@pytest.mark.asyncio
async def test_b12_maybe_compact_context_noop_under_budget():
    from backend.agent.nodes import maybe_compact_context
    from backend.agent.state import AgentState, Message

    state = AgentState(session_id="b12-noop")
    state.add_message(Message.user("short"))
    assert await maybe_compact_context(state, SummarizingLLM(), max_tokens=10_000_000) is False
    assert len(state.messages) == 1


# ══════════════════════════════════════════════════════════════
# A2: 只读并发 / 写操作串行
# ══════════════════════════════════════════════════════════════

def _read_only_state(n: int) -> AgentState:
    state = AgentState(session_id="a2")
    for i in range(n):
        state.tool_invocations.append(ToolInvocation(
            id=f"call_{i}", name="file_rw",
            arguments={"operation": "read", "path": f"f{i}.py"},
        ))
    return state


@pytest.mark.asyncio
async def test_a2_read_only_tools_run_concurrently():
    state = _read_only_state(4)
    delay = 0.15
    started = time.time()

    async def handler(name, args, ws):
        await asyncio.sleep(delay)
        return {"success": True, "output": args["path"]}

    await executor_node(state, handler, ".")
    elapsed = time.time() - started

    # 串行需 4*0.15=0.6s；并发应远低于它
    assert elapsed < delay * 3, f"expected concurrent execution, took {elapsed:.3f}s"
    # gather 保持输入顺序
    assert [r.invocation_id for r in state.tool_results] == ["call_0", "call_1", "call_2", "call_3"]
    assert [r.output for r in state.tool_results] == ["f0.py", "f1.py", "f2.py", "f3.py"]


@pytest.mark.asyncio
async def test_a2_mutating_tool_forces_serial():
    state = AgentState(session_id="a2-write")
    state.tool_invocations = [
        ToolInvocation(id="w0", name="apply_patch", arguments={"file_path": "a.py"}),
        ToolInvocation(id="w1", name="apply_patch", arguments={"file_path": "a.py"}),
    ]
    order = []

    async def handler(name, args, ws):
        order.append(args["file_path"])
        await asyncio.sleep(0.02)
        return {"success": True, "output": "ok"}

    await executor_node(state, handler, ".")

    # 串行执行 -> 顺序严格为输入顺序
    assert order == ["a.py", "a.py"]
    assert [r.invocation_id for r in state.tool_results] == ["w0", "w1"]


@pytest.mark.asyncio
async def test_a2_mixed_batch_falls_back_to_serial():
    """一批里只要有一个写操作，整批串行（安全优先）。"""
    state = AgentState(session_id="a2-mixed")
    state.tool_invocations = [
        ToolInvocation(id="r0", name="file_rw", arguments={"operation": "read", "path": "a.py"}),
        ToolInvocation(id="w0", name="file_rw", arguments={"operation": "write", "path": "a.py"}),
    ]
    active = 0
    max_active = 0

    async def handler(name, args, ws):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return {"success": True, "output": "ok"}

    await executor_node(state, handler, ".")

    assert max_active == 1, "mixed batch must run serially"


@pytest.mark.asyncio
async def test_a2_empty_pending_returns_empty():
    state = AgentState(session_id="a2-empty")
    result = await executor_node(state, lambda *a: None, ".")
    assert result == {"tool_results": []}


@pytest.mark.asyncio
async def test_a2_skips_already_executed_invocations():
    from backend.agent.state import ToolResult

    state = AgentState(session_id="a2-resume")
    state.tool_invocations = [
        ToolInvocation(id="done", name="list_files", arguments={}),
        ToolInvocation(id="todo", name="list_files", arguments={}),
    ]
    state.tool_results = [ToolResult(invocation_id="done", name="list_files", output="old", success=True)]
    calls = []

    async def handler(name, args, ws):
        calls.append("hit")
        return {"success": True, "output": "new"}

    await executor_node(state, handler, ".")
    assert calls == ["hit"]
    assert len(state.tool_results) == 2  # 只新增了 todo


@pytest.mark.asyncio
async def test_a2_post_edit_hooks_run_for_every_result(monkeypatch):
    """并发/串行后，LSP 与安全扫描后处理仍对每个成功结果执行。"""
    import backend.agent.nodes as nodes

    state = AgentState(session_id="a2-hooks")
    state.tool_invocations = [
        ToolInvocation(id="p0", name="apply_patch", arguments={"file_path": "a.py"}),
        ToolInvocation(id="p1", name="apply_patch", arguments={"file_path": "b.py"}),
    ]
    seen = []

    async def lsp(name, args, result):
        seen.append(("lsp", args["file_path"]))
        return " [lsp]"

    async def sec(path):
        seen.append(("sec", path))
        return " [sec]"

    monkeypatch.setattr(nodes, "post_file_edit_hook", lsp)
    monkeypatch.setattr(nodes, "post_edit_security_hook", sec)

    async def handler(name, args, ws):
        return {"success": True, "output": "patched"}

    await executor_node(state, handler, ".")

    assert ("lsp", "a.py") in seen and ("lsp", "b.py") in seen
    assert ("sec", "a.py") in seen and ("sec", "b.py") in seen
    assert all("lsp" in r.output for r in state.tool_results)
    # state.messages 里的 tool 消息也应带上注解
    tool_msgs = [m for m in state.messages if m.role == "tool"]
    assert all("lsp" in m.content for m in tool_msgs)
