# 回归测试 — 第二轮修复（审批卡片后端契约 / shell 沙箱 / cron 持久化 / hooks 去 no-op）
import sys, asyncio, json, time
from pathlib import Path

import pytest


# ── 1. shell workspace-only 边界检查 ───────────────────────────────
class TestShellWorkspaceBoundary:
    def test_escape_patterns_detected(self):
        from backend.tools.shell_command import _violates_workspace
        assert _violates_workspace("cd /etc") is not None
        assert _violates_workspace("cd ..") is not None
        assert _violates_workspace("echo x > /tmp/out") is not None
        assert _violates_workspace("echo x > C:\\Windows\\temp.txt") is not None
        assert _violates_workspace("cat /etc/passwd") is not None

    def test_safe_commands_allowed(self):
        from backend.tools.shell_command import _violates_workspace
        assert _violates_workspace("ls -la") is None
        assert _violates_workspace("python -m pytest") is None
        assert _violates_workspace("git status") is None
        assert _violates_workspace("") is None

    @pytest.mark.asyncio
    async def test_handler_blocks_escape_with_boundary(self):
        from backend.tools.shell_command import shell_handler
        result = await shell_handler({"command": "cd /etc && ls", "_workspace_boundary": True}, ".")
        assert result["success"] is False
        assert "workspace-only" in result["stderr"]

    @pytest.mark.asyncio
    async def test_handler_passes_without_boundary(self):
        from backend.tools.shell_command import shell_handler
        from backend.approval import approval_bridge, ApprovalPolicy
        # 本用例验证「无 _workspace_boundary 标志时不因该标志报错」，与审批无关。
        # 生产路径一定经 AgentGraph 注入 _approval_policy；裸调 handler 需显式给
        # 策略，否则会落到默认 on-request 并阻塞 30s 后拒绝。
        prev = approval_bridge.manager.policy
        approval_bridge.manager.set_policy(ApprovalPolicy.NEVER)
        try:
            result = await shell_handler({"command": "echo ok", "timeout": 5}, ".")
        finally:
            approval_bridge.manager.set_policy(prev)
        assert result["success"] is True
        assert "ok" in result["stdout"]


# ── 2. cron 待触发持久化 → 已移至 tests/test_fixes_cron.py（隔离运行） ──

# ── 3. hooks/register 去 no-op ─────────────────────────────────────
class TestHooksReal:
    def test_builtin_sse_notify_hook_exists(self):
        from backend.hooks_system import builtin_sse_notify_hook, HookContext
        ctx = HookContext(tool_name="test")
        result = builtin_sse_notify_hook(ctx)
        assert result.allow is True

    @pytest.mark.asyncio
    async def test_register_endpoint_registers_real_hook(self):
        from backend.hooks_system import get_hook_registry, HookPoint, builtin_sse_notify_hook
        registry = get_hook_registry()
        hook_id = registry.register(HookPoint.POST_MODEL_OUTPUT, builtin_sse_notify_hook, async_cb=True)
        assert hook_id.startswith("hook_")
        assert any(cb is builtin_sse_notify_hook for cb in registry._async_hooks[HookPoint.POST_MODEL_OUTPUT])


# ── 4. 多 Agent 工具参数校验 ──────────────────────────────────────
class TestMultiAgentToolValidation:
    @pytest.mark.asyncio
    async def test_spawn_requires_task(self):
        from backend.tools.multi_agent_tools import spawn_agent_handler
        result = await spawn_agent_handler({"name": "x"}, ".")
        assert result["success"] is False
        assert "task" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_wait_agents_empty(self):
        from backend.tools.multi_agent_tools import wait_agents_handler
        result = await wait_agents_handler({"agent_ids": [], "timeout": 1}, ".")
        assert result["success"] is True


# ── 5. computer_use 工具 schema 含 set_value ───────────────────────
class TestComputerUseSchema:
    def test_set_value_in_enum(self):
        from backend.tools.computer_use import COMPUTER_USE_SPEC
        enum = COMPUTER_USE_SPEC.parameters["properties"]["method"]["enum"]
        assert "set_value" in enum
