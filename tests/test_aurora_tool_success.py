"""Aurora 工具成功判定与 schema 暴露策略的回归测试。

这些用例锁定两个静默失败：工具返回错误字符串时不能伪报成功，且每轮不能无条件发送全部工具 schema。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.tools import tool_registry  # noqa: E402
from backend.tools.base import ToolSpec, _looks_like_error  # noqa: E402


@pytest.mark.parametrize(
    "text",
    [
        "Error: Could not parse any file changes from the patch.",
        "Error: No patch content provided.",
        "Command rejected: shell chaining is not allowed",
        "Traceback (most recent call last):",
        "Permission denied: /etc/passwd",
        "Patch rejected by approval gate",
    ],
)
def test_error_prefix_detection_only_marks_failure_prefixes(text):
    """锁定工具错误前缀判定，避免把正常源码内容误判为失败。

    不能使用宽松的 ``'error' in text``：成功读取的源码经常包含
    ``error``，宽判据会把成功读操作标成失败并触发无意义重试，结果比漏报
    更糟。只有返回值开头的约定错误前缀才代表工具失败。
    """
    assert _looks_like_error(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "# error handling section",
        "def handle_error():",
        "Successfully wrote 3 lines to a.py",
    ],
)
def test_error_prefix_detection_keeps_normal_file_content_successful(text):
    """锁定包含 error 单词的正常文件内容不会被误报成工具失败。"""
    assert _looks_like_error(text) is False


@pytest.mark.asyncio
async def test_registry_string_error_result_is_failure_and_normal_string_is_success():
    """锁定 registry 不再把字符串错误结果静默记录为 SUCCESS。"""
    name = "__aurora_string_result_regression__"
    previous_spec = tool_registry.get_tool(name)
    previous_handler = tool_registry._handlers.get(name)

    async def handler(arguments, workspace):
        return arguments["result"]

    tool_registry.register(
        ToolSpec(name=name, description="test-only", parameters={"type": "object"}),
        handler,
    )
    try:
        failed = await tool_registry.execute(
            name, {"result": "Error: Could not parse any file changes from the patch."}
        )
        succeeded = await tool_registry.execute(
            name, {"result": "Successfully wrote 3 lines to a.py"}
        )

        assert failed.success is False
        assert failed.error
        assert "Could not parse" in failed.error
        assert succeeded.success is True
        assert succeeded.error is None
    finally:
        if previous_spec is not None and previous_handler is not None:
            tool_registry.register(previous_spec, previous_handler)
        else:
            tool_registry.unregister(name)


def test_default_openai_exposure_is_small_but_keeps_core_coding_tools():
    """锁定默认 schema 只暴露 direct，同时保持核心编码工具可达。"""
    direct = tool_registry.list_tools_openai()
    full = tool_registry.list_tools_openai(exposures=None)
    all_specs = tool_registry.list_tools(exposures=None)

    direct_names = {item["function"]["name"] for item in direct}
    full_names = {item["function"]["name"] for item in full}
    exposure_by_name = {spec.name: spec.exposure for spec in all_specs}
    core_tools = {
        "shell_command",
        "file_rw",
        "code_search",
        "apply_patch",
        "list_files",
        "git_ops",
        "plan_update",
        "todo_write",
        "verify_plan",
        "web_fetch",
        "web_search",
        "lsp",
        "send_message",
    }

    assert direct
    assert all(exposure_by_name[name] == "direct" for name in direct_names)
    assert all(
        name not in direct_names
        for name, exposure in exposure_by_name.items()
        if exposure in {"deferred", "hidden"}
    )
    assert core_tools <= direct_names
    assert full_names == {spec.name for spec in all_specs}
    assert direct_names < full_names

    direct_chars = len(json.dumps(direct, ensure_ascii=False))
    full_chars = len(json.dumps(full, ensure_ascii=False))
    assert direct_chars < full_chars * 0.75
    assert any(exposure in {"deferred", "hidden"} for exposure in exposure_by_name.values())
