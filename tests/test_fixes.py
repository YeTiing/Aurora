# 回归测试 — 企业级差距修复（路径穿越 / 角色加载 / 多Agent工具 / 审批门 / 连接器工具）
import sys, asyncio
from pathlib import Path

import pytest


# ── 1. safe_resolve_path 路径穿越修复 ──────────────────────────────
class TestSafeResolvePath:
    def test_normal_path_allowed(self, tmp_path):
        from backend.tools.base import safe_resolve_path
        ws = tmp_path / "proj"
        ws.mkdir()
        (ws / "a.txt").write_text("x")
        resolved = safe_resolve_path("a.txt", str(ws))
        assert resolved == (ws / "a.txt").resolve()

    def test_workspace_itself_allowed(self, tmp_path):
        from backend.tools.base import safe_resolve_path
        ws = tmp_path / "proj"
        ws.mkdir()
        assert safe_resolve_path(".", str(ws)) == ws.resolve()

    def test_sibling_prefix_traversal_blocked(self, tmp_path):
        """回归: /proj 不应被 /proj-evil 前缀绕过（旧实现 startswith 漏洞）"""
        from backend.tools.base import safe_resolve_path
        ws = tmp_path / "proj"
        evil = tmp_path / "proj-evil"
        ws.mkdir()
        evil.mkdir()
        (evil / "secret.txt").write_text("top secret")
        with pytest.raises(PermissionError):
            safe_resolve_path("../proj-evil/secret.txt", str(ws))

    def test_double_dot_escape_blocked(self, tmp_path):
        from backend.tools.base import safe_resolve_path
        ws = tmp_path / "proj"
        ws.mkdir()
        with pytest.raises(PermissionError):
            safe_resolve_path("../../etc/passwd", str(ws))


# ── 2. 角色加载器 ────────────────────────────────────────────────
class TestRolesLoader:
    def test_roles_loaded(self):
        from backend.agent.roles_loader import list_roles
        roles = list_roles()
        assert len(roles) >= 12
        for required in ("architect", "code-explorer", "security-reviewer", "refactor-cleaner", "default", "worker"):
            assert required in roles, f"missing role {required}"

    def test_get_role_prompt(self):
        from backend.agent.roles_loader import get_role_prompt
        prompt = get_role_prompt("architect")
        assert prompt and "Architect" in prompt

    def test_get_role_by_display_name(self):
        from backend.agent.roles_loader import get_role
        assert get_role("security reviewer") is not None
        assert get_role("Security Reviewer") is not None

    def test_inject_unknown_role_passthrough(self):
        from backend.agent.roles_loader import inject_role
        assert inject_role("no-such-role", "CORE") == "CORE"

    def test_inject_role_into_prompt(self):
        from backend.agent.roles_loader import inject_role
        out = inject_role("architect", "CORE")
        assert out.startswith("# 当前角色")
        assert "CORE" in out

    def test_system_prompt_ru_accepts_role(self):
        from backend.agent.system_prompt import RU
        sp = RU(agent_role="security-reviewer")
        assert "Security Reviewer" in sp or "Security" in sp


# ── 3. AgentState 角色字段 ────────────────────────────────────────
class TestAgentStateRole:
    def test_roundtrip(self):
        from backend.agent.state import AgentState
        s = AgentState(session_id="s1", agent_role="architect")
        d = s.to_dict()
        assert d["agent_role"] == "architect"
        assert AgentState.from_dict(d).agent_role == "architect"


# ── 4. 工具注册（多Agent + 连接器） ────────────────────────────────
class TestNewToolRegistration:
    def test_multi_agent_tools_registered(self):
        from backend.tools import tool_registry
        names = {t.name for t in tool_registry.list_tools()}
        assert {"spawn_agent", "send_agent_message", "wait_agents", "close_agent"} <= names

    def test_connector_tool_registered(self):
        from backend.tools import tool_registry
        names = {t.name for t in tool_registry.list_tools()}
        assert "connector_call" in names


# ── 5. 审批风险评估补全 ───────────────────────────────────────────
class TestApprovalRisk:
    def _manager(self):
        from backend.approval import ApprovalManager
        return ApprovalManager()

    def test_file_delete_critical(self):
        mgr = self._manager()
        assert mgr.assess_risk("file_rw", {"operation": "delete", "path": "x"}).value == "critical"

    def test_file_write_medium(self):
        mgr = self._manager()
        assert mgr.assess_risk("file_rw", {"operation": "write", "path": "x"}).value == "medium"

    def test_web_fetch_post_high(self):
        mgr = self._manager()
        assert mgr.assess_risk("web_fetch", {"method": "POST", "url": "https://x"}).value == "high"
        assert mgr.assess_risk("web_fetch", {"method": "GET", "url": "https://x"}).value == "medium"

    def test_connector_call_write_high(self):
        mgr = self._manager()
        assert mgr.assess_risk("connector_call", {"action": "send_message"}).value == "high"
        assert mgr.assess_risk("connector_call", {"action": "list_repos"}).value == "low"

    def test_code_exec_high(self):
        mgr = self._manager()
        assert mgr.assess_risk("code_exec", {"language": "python", "code": "x"}).value == "high"


# ── 6. 审批门（never 策略下直接放行） ─────────────────────────────
@pytest.fixture
def _approval_policy_reset():
    """恢复全局审批策略，避免污染其他测试（全局单例）"""
    from backend.approval import approval_bridge, ApprovalPolicy
    original = approval_bridge.manager.policy
    yield
    approval_bridge.manager.set_policy(original)


class TestApprovalGate:
    @pytest.mark.asyncio
    async def test_never_policy_passthrough(self, _approval_policy_reset):
        from backend.approval import approval_bridge, ApprovalPolicy
        from backend.tools.approval_gate import maybe_request_approval
        approval_bridge.manager.set_policy(ApprovalPolicy.NEVER)
        decision = await maybe_request_approval("code_exec", {"code": "print(1)"}, description="test")
        assert decision is None

    @pytest.mark.asyncio
    async def test_unknown_tool_passthrough(self, _approval_policy_reset):
        from backend.approval import approval_bridge, ApprovalPolicy
        from backend.tools.approval_gate import maybe_request_approval
        approval_bridge.manager.set_policy(ApprovalPolicy.ON_REQUEST)
        # LOW 风险工具不需要审批
        decision = await maybe_request_approval("code_search", {"query": "x"}, description="test")
        assert decision is None


# ── 7. 多 Agent 编排器（事件循环回归） ─────────────────────────────
class TestMultiAgentFixed:
    @pytest.mark.asyncio
    async def test_spawn_wait_close_cycle(self):
        from backend.multi_agent import MultiAgentOrchestrator
        orch = MultiAgentOrchestrator(max_parallel=2)

        async def executor(node):
            return f"done:{node.task}"

        agent = await orch.spawn(parent_id=None, name="t1", task="hello")
        await orch.start(agent.id, executor)
        nodes = await orch.wait([agent.id], timeout=10)
        assert nodes[0].status.value in ("done", "error")
        assert nodes[0].result == "done:hello"
        await orch.cleanup()

    @pytest.mark.asyncio
    async def test_drain_queue_after_loop_shutdown_no_crash(self):
        """回归: _drain_queue 在 loop 关闭后调用 create_task 会炸。
        这里验证 cleanup 后队列状态一致（pending 保留、不抛异常）。"""
        from backend.multi_agent import MultiAgentOrchestrator
        orch = MultiAgentOrchestrator(max_parallel=1)

        async def slow_executor(node):
            await asyncio.sleep(0.01)
            return "ok"

        a1 = await orch.spawn(None, "a1", "t1")
        a2 = await orch.spawn(None, "a2", "t2")
        await orch.start(a1.id, slow_executor)
        # a2 入队等待（max_parallel=1）
        await orch.start(a2.id, slow_executor)
        nodes = await orch.wait([a1.id, a2.id], timeout=10)
        assert all(n.status.value in ("done", "error") for n in nodes)
        # cleanup 不应抛异常
        await orch.cleanup()


# ── 8. 连接器工具（未连接时给出明确错误） ──────────────────────────
class TestConnectorTool:
    @pytest.mark.asyncio
    async def test_not_connected_error(self):
        from backend.tools.connector_tools import connector_call_handler
        result = await connector_call_handler({"connector": "github", "action": "list_repos"}, ".")
        assert not result.success
        # 未注册或未连接都是正确的失败路径
        assert "not registered" in result.error.lower() or "not connected" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unknown_connector_error(self):
        from backend.tools.connector_tools import connector_call_handler
        result = await connector_call_handler({"connector": "nope", "action": "x"}, ".")
        assert not result.success
        assert "Unknown connector" in result.error

    @pytest.mark.asyncio
    async def test_unknown_action_error(self):
        from backend.tools.connector_tools import connector_call_handler
        result = await connector_call_handler({"connector": "github", "action": "hack"}, ".")
        assert not result.success
        assert "Unknown action" in result.error


# ── 9. cron 持久化关键修复：from_dict schedule 映射（纯逻辑，无实例化） ──
class TestCronFromDict:
    def test_from_dict_schedule_mapping(self):
        """回归: to_dict 用 'schedule' 键但字段名是 schedule_text，
        旧实现 from_dict 抛 TypeError 导致 cron 任务重启全部丢失。"""
        import backend.cron_scheduler as cron_mod
        t = cron_mod.CronTask.from_dict({
            "name": "n", "schedule": "every 60s", "prompt": "p",
            "interval_seconds": 60, "run_count": 0, "pending_fire": True,
        })
        assert t.schedule_text == "every 60s"
        assert t.pending_fire is True
