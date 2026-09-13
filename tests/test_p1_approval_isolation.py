"""审批策略的会话隔离 —— 防止并发会话互相覆盖安全策略。

原始缺陷：AgentGraph._apply_approval_mode 把每个请求的 approval_mode 写到
进程级单例 approval_bridge.manager 上。并发会话下后到的请求会覆盖先到的，
即会话 A 的 never 可能盖掉会话 B 的 untrusted —— 安全策略被静默降级。

修复后：策略随 state 传递，由 _run_executor 注入 args["_approval_policy"]，
工具侧优先读取该请求级值。
"""
import pytest

from backend.approval import ApprovalPolicy, RiskLevel
from backend.tools.approval_gate import (
    resolve_approval_policy,
    should_request_approval,
)


class _Mgr:
    """最小 manager 替身：只暴露 policy 与无状态判断方法。"""

    def __init__(self, policy):
        self.policy = policy

    @staticmethod
    def policy_needs_approval(policy, risk, tool_name=""):
        if policy == ApprovalPolicy.NEVER:
            return False
        if policy == ApprovalPolicy.UNTRUSTED:
            return True
        if policy == ApprovalPolicy.ON_REQUEST:
            return risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        return False


def test_request_policy_overrides_global():
    """请求级策略必须压过全局单例 —— 这是隔离的核心。"""
    mgr = _Mgr(ApprovalPolicy.ON_REQUEST)

    # 全局是 on-request（HIGH 要审批），但该请求声明 never
    assert should_request_approval(
        mgr, "shell_command", {"_approval_policy": "never"}, RiskLevel.HIGH
    ) is False


def test_request_policy_can_be_stricter_than_global():
    """反向也必须成立：请求更严格时不能被全局的宽松策略放行。"""
    mgr = _Mgr(ApprovalPolicy.NEVER)

    assert should_request_approval(
        mgr, "code_search", {"_approval_policy": "untrusted"}, RiskLevel.LOW
    ) is True


def test_two_sessions_do_not_interfere():
    """模拟两个并发会话：一个 never 一个 untrusted，互不影响。"""
    mgr = _Mgr(ApprovalPolicy.ON_REQUEST)

    session_a = {"_approval_policy": "never"}
    session_b = {"_approval_policy": "untrusted"}

    # 交错调用，顺序不影响结果（修复前读的是同一个 mgr.policy，会互相污染）
    assert should_request_approval(mgr, "shell_command", session_a, RiskLevel.HIGH) is False
    assert should_request_approval(mgr, "shell_command", session_b, RiskLevel.HIGH) is True
    assert should_request_approval(mgr, "shell_command", session_a, RiskLevel.HIGH) is False
    assert should_request_approval(mgr, "shell_command", session_b, RiskLevel.HIGH) is True


def test_missing_policy_falls_back_to_global():
    """无请求级策略时回落到全局，且不抛异常。"""
    mgr = _Mgr(ApprovalPolicy.UNTRUSTED)
    assert should_request_approval(mgr, "shell_command", {}, RiskLevel.LOW) is True


def test_invalid_policy_value_falls_back_not_crash():
    """非法策略串不应导致崩溃，应回落全局。"""
    mgr = _Mgr(ApprovalPolicy.ON_REQUEST)
    assert resolve_approval_policy(mgr, {"_approval_policy": "bogus"}) == ApprovalPolicy.ON_REQUEST


def test_manager_without_stateless_api_still_works():
    """兼容只实现 needs_approval 的旧式 manager（如测试替身/外部注入）。"""

    class LegacyManager:
        def __init__(self):
            self.policy = ApprovalPolicy.NEVER
            self.called = False

        def needs_approval(self, risk, tool_name=""):
            self.called = True
            return True

    m = LegacyManager()
    assert should_request_approval(m, "shell_command", {}, RiskLevel.HIGH) is True
    assert m.called is True


@pytest.mark.asyncio
async def test_graph_does_not_mutate_global_policy():
    """AgentGraph 构造/运行不得改写进程级策略（原缺陷的直接回归测试）。"""
    from backend.approval import approval_bridge
    from backend.agent.graph import AgentGraph
    from backend.agent.llm_client import MockLLMClient

    original = approval_bridge.manager.policy

    async def tool_handler(name, args, ws):
        return {"success": True, "output": "ok", "error": None}

    graph = AgentGraph(
        llm=MockLLMClient(),
        tool_handler=tool_handler,
        tools_schema=[],
        max_turns=1,
        workspace=".",
    )
    # 即便传入 never，也不得把全局单例改成 never
    graph._apply_approval_mode("never")
    assert approval_bridge.manager.policy == original


@pytest.mark.asyncio
async def test_state_carries_approval_mode():
    """策略必须落在 state 上，供 _run_executor 注入 args。"""
    from backend.agent.graph import AgentGraph
    from backend.agent.llm_client import MockLLMClient

    async def tool_handler(name, args, ws):
        return {"success": True, "output": "ok", "error": None}

    graph = AgentGraph(
        llm=MockLLMClient(), tool_handler=tool_handler, tools_schema=[], max_turns=1
    )
    state = await graph.run("hello", session_id="s-approval", approval_mode="untrusted")
    assert state.approval_mode == "untrusted"
