"""Aurora agent loop 合同的回归测试。

这些用例锁定 tool-call 历史、planner 解析、工具提示压缩、workspace 选择和轮次响应中的静默失败。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.graph import AgentGraph  # noqa: E402
from backend.agent.llm_client import MockLLMClient  # noqa: E402
from backend.agent.nodes import (  # noqa: E402
    _looks_like_plan,
    _parse_plan_json,
    _tools_digest,
    _with_type,
)
from backend.agent.state import AgentState  # noqa: E402
from backend.api import deps  # noqa: E402
from backend.api.models import AgentResponse, ChatRequest  # noqa: E402
from backend.api.routes import chat as chat_routes  # noqa: E402
from backend.tools import tool_registry  # noqa: E402


def test_with_type_preserves_typed_entries_and_normalizes_flat_entries():
    """锁定下一轮请求所需的 OpenAI tool-call type 字段不会从历史中丢失。"""
    typed = {
        "id": "typed-1",
        "type": "function",
        "function": {"name": "shell_command", "arguments": "{}"},
    }
    flat = {"id": "flat-1", "name": "file_rw", "arguments": "{\"path\": \"a.py\"}"}

    result = _with_type([typed, flat])

    assert result[0] is typed
    assert result[0] == typed
    assert result[1] == {
        "id": "flat-1",
        "type": "function",
        "function": {"name": "file_rw", "arguments": "{\"path\": \"a.py\"}"},
    }
    assert all(item["type"] == "function" for item in result)


def test_with_type_handles_empty_none_and_non_dict_entries():
    """锁定异常形状的 provider tool-call 不会让 agent loop 抛错。"""
    assert _with_type(None) == []
    assert _with_type([]) == []
    assert _with_type(["not a call", 42, None]) == []


@pytest.mark.parametrize(
    "blob",
    [
        '<invoke name="shell_command">{"command":"pwd"}</invoke>',
        '<tool_calls>[{"name":"file_rw"}]</tool_calls>',
    ],
)
def test_parse_plan_rejects_tool_markup_instead_of_making_fake_step(blob):
    """锁定 planner 的 XML 工具调用文本不会被伪装成一个计划步骤。"""
    parsed = _parse_plan_json(blob)

    assert parsed == []
    assert _looks_like_plan(parsed) is False


def test_parse_plan_accepts_json_array_and_plan_wrappers():
    """锁定合法 JSON 数组及 plan/steps/tasks 包装对象仍能生成真实计划。"""
    expected = [{"step": 1, "description": "Read the target file", "tool": "file_rw"}]

    assert _parse_plan_json(json.dumps(expected)) == expected
    for key in ("plan", "steps", "tasks"):
        assert _parse_plan_json(json.dumps({key: expected})) == expected


def test_parse_plan_keeps_numbered_list_fallback_and_real_plan_detection():
    """锁定无 JSON 时的编号列表回退，同时区分真实计划与工具标记。"""
    parsed = _parse_plan_json("1. inspect the file\n2. apply the fix")

    assert parsed == [
        {"description": "inspect the file"},
        {"description": "apply the fix"},
    ]
    assert _looks_like_plan(parsed) is True
    assert _looks_like_plan([{"description": '<tool_calls name="shell_command">'}]) is False


def test_tools_digest_is_compact_complete_and_handles_missing_descriptions():
    """锁定工具摘要不重复嵌入完整 schema，且不会让任何工具名称不可发现。

    ⚠️ 这里刻意用**默认** `max_chars` 跑名称检查：过窄的预算若按字符硬切，
    会把最后一个工具名劈成两半；若把「已列出」的数量也数进「另有 N 个」，
    还会告诉模型它看到的比实际更少。两者都是静默误导。
    """
    direct_schema = tool_registry.list_tools_openai()
    full_schema = tool_registry.list_tools_openai(exposures=None)
    pretty_schema_chars = len(json.dumps(full_schema, ensure_ascii=False, indent=2))
    direct_digest = _tools_digest(direct_schema)     # 默认 max_chars
    full_digest = _tools_digest(full_schema)         # 默认 max_chars

    assert direct_digest
    assert len(full_digest) < pretty_schema_chars / 3

    # direct 工具只有 13 个，默认预算下必须**全部**可见，不该有任何截断提示
    for item in direct_schema:
        assert f"- {item['function']['name']}" in direct_digest
    assert "未列出" not in direct_digest, "direct 工具被截断了，模型会看不到核心工具"

    # 全量（含 deferred/hidden）可以截断，但必须：完整行 + 只数未列出的
    listed = {ln.split(":", 1)[0][2:].strip()
              for ln in full_digest.splitlines() if ln.startswith("- ")}
    all_names = {i["function"]["name"] for i in full_schema}
    assert listed <= all_names, f"列出了不存在的工具（说明行被劈断）: {listed - all_names}"
    if "未列出" in full_digest:
        import re as _re
        m = _re.search(r"另有\s*(\d+)\s*个", full_digest)
        assert m, "截断提示缺少可解析的计数"
        assert int(m.group(1)) == len(all_names) - len(listed), (
            "「另有 N 个」把已列出的也数进去了，会误导模型以为可见的更少"
        )
    else:
        assert listed == all_names

    assert _tools_digest([{"type": "function", "function": {"name": "no_description"}}]) == "- no_description"
    assert _tools_digest([]) == ""
    assert _tools_digest(None) == ""


@pytest.mark.asyncio
async def test_explicit_workspace_is_resolved_and_not_replaced_by_graph_default(tmp_path):
    """锁定请求传入的绝对 workspace，而不是 AgentGraph 配置目录被使用。

    这里采用最小真实 AgentGraph 构造并执行一次短对话：只用 MockLLM，直接检查
    返回 state.workspace，避免仅靠源码字符串断言而漏掉实际运行路径。
    """
    configured = tmp_path / "configured"
    explicit = tmp_path / "explicit"
    configured.mkdir()
    explicit.mkdir()

    async def noop_handler(name, arguments, workspace):
        return {"success": True, "output": "ok"}

    graph = AgentGraph(
        llm=MockLLMClient(),
        tool_handler=noop_handler,
        tools_schema=[],
        workspace=str(configured),
    )
    state = await graph.run(
        "hello",
        session_id="workspace-contract",
        workspace=str(explicit),
    )

    assert Path(state.workspace) == explicit.resolve()
    assert Path(state.workspace) != configured.resolve()


def test_agent_response_exposes_turns_with_zero_default():
    """锁定消费者读取 turns 时既能获得真实值，也保留旧响应的 0 默认值。"""
    default_response = AgentResponse(session_id="s", response="ok")
    response = AgentResponse(session_id="s", response="ok", turns=7)

    assert default_response.turns == 0
    assert response.turns == 7


@pytest.mark.asyncio
async def test_chat_populates_agent_response_turns_from_state(monkeypatch):
    """锁定 chat 路由把 state.total_turns 传递给 AgentResponse，而不是恒为 0。"""
    class FakeGraph:
        async def run(self, *args, **kwargs):
            state = AgentState(session_id=kwargs["session_id"], workspace=kwargs["workspace"])
            state.final_response = "done"
            state.total_turns = 9
            return state

    monkeypatch.setattr(chat_routes, "_build_full_prompt", lambda message: message)
    monkeypatch.setattr(deps, "get_graph_for", lambda session_id: FakeGraph())

    response = await chat_routes.chat(
        ChatRequest(message="hello", session_id="turn-contract", workspace=str(Path.cwd()))
    )

    assert response.response == "done"
    assert response.turns == 9
