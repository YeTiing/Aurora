import pytest

from backend.approval import ApprovalBridge, ApprovalManager, ApprovalPolicy, RiskLevel
from backend.hooks_system import HookContext, HookPoint, builtin_approval_hook
from backend.tools.shell_command import shell_handler


@pytest.mark.asyncio
async def test_approval_bridge_emits_request_and_decision_events():
    events = []

    async def emit(event):
        events.append(event.to_dict())

    manager = ApprovalManager(policy=ApprovalPolicy.ON_REQUEST)
    bridge = ApprovalBridge(manager=manager, event_emit=emit)

    request = await bridge.request_command_approval(
        session_id="session-approval",
        thread_id="thread-approval",
        command="npm run build",
        risk=RiskLevel.HIGH,
        description="Run desktop build",
    )
    assert request.status == "pending"

    decision = await bridge.decide(request.id, "approve", session_id="session-approval", thread_id="thread-approval")

    assert decision == {"request_id": request.id, "action": "approve", "ok": True}
    assert manager.stats()["approved"] == 1
    assert [event["type"] for event in events] == [
        "codex/event/exec_approval_request",
        "codex/event/thread_follower_command_approval_decision",
    ]
    assert events[0]["data"]["command"] == "npm run build"
    assert events[1]["data"]["decision"] == "approve"


@pytest.mark.asyncio
async def test_approval_bridge_emits_file_decision_for_patch_requests():
    events = []

    async def emit(event):
        events.append(event.to_dict())

    manager = ApprovalManager(policy=ApprovalPolicy.ON_REQUEST)
    bridge = ApprovalBridge(manager=manager, event_emit=emit)

    request = await bridge.request_file_approval(
        session_id="session-file",
        thread_id="thread-file",
        file_path="desktop/src/main/index.ts",
        description="Apply thread control patch",
    )
    decision = await bridge.decide(request.id, "deny", session_id="session-file", thread_id="thread-file")

    assert decision == {"request_id": request.id, "action": "deny", "ok": True}
    assert manager.stats()["denied"] == 1
    assert [event["type"] for event in events] == [
        "codex/event/apply_patch_approval_request",
        "codex/event/thread_follower_file_approval_decision",
    ]


@pytest.mark.asyncio
async def test_builtin_hook_uses_approval_bridge_for_real_tool_path(monkeypatch):
    captured = []

    async def fake_request_command_approval(session_id, thread_id, command, risk, description):
        captured.append({
            "session_id": session_id,
            "thread_id": thread_id,
            "command": command,
            "risk": risk,
            "description": description,
        })

    class FakeManager:
        def assess_risk(self, tool_name, arguments):
            return RiskLevel.HIGH

        def needs_approval(self, risk, tool_name):
            return True

    class FakeBridge:
        manager = FakeManager()
        request_command_approval = staticmethod(fake_request_command_approval)

    import backend.approval as approval_module
    monkeypatch.setattr(approval_module, "approval_bridge", FakeBridge())

    result = await builtin_approval_hook(HookContext(
        hook_point=HookPoint.PRE_TOOL_EXEC,
        session_id="session-hook",
        thread_id="thread-hook",
        tool_name="shell_command",
        tool_args={"command": "npm run build"},
    ))

    assert result.allow is True
    assert captured == [{
        "session_id": "session-hook",
        "thread_id": "thread-hook",
        "command": "npm run build",
        "risk": RiskLevel.HIGH,
        "description": "shell_command: {'command': 'npm run build'}",
    }]


@pytest.mark.asyncio
async def test_shell_handler_uses_approval_bridge_without_auto_approving(monkeypatch, tmp_path):
    captured = []

    async def fake_request_command_approval(**kwargs):
        captured.append(kwargs)

    class FakeManager:
        def assess_risk(self, tool_name, arguments):
            return RiskLevel.HIGH

        def needs_approval(self, risk, tool_name):
            return True

        async def wait_for_decision(self, request_id, timeout):
            return "approved"

    class FakeRequest:
        id = "request-shell"
        timeout = 30

    class FakeBridge:
        manager = FakeManager()

        @staticmethod
        async def request_command_approval(**kwargs):
            captured.append(kwargs)
            return FakeRequest()

    import backend.approval as approval_module
    monkeypatch.setattr(approval_module, "approval_bridge", FakeBridge())

    result = await shell_handler(
        {"command": "echo ok", "timeout": 5, "session_id": "session-shell", "thread_id": "thread-shell"},
        workspace=str(tmp_path),
    )

    assert result["success"] is True
    assert captured[0]["session_id"] == "session-shell"
    assert captured[0]["thread_id"] == "thread-shell"
    assert captured[0]["command"] == "echo ok"
