import pytest

from backend.thread_follower import ThreadFollower, ThreadSettings


@pytest.mark.asyncio
async def test_steer_interrupt_and_compact_thread_emit_codex_events():
    events = []

    async def emit(event):
        events.append(event.to_dict())

    follower = ThreadFollower(event_emit=emit)
    await follower.start_turn(
        thread_id="thread-1",
        session_id="session-1",
        message="Build the desktop app",
        settings=ThreadSettings(model="gpt-5.5", reasoning_effort="high"),
    )

    steer_response = await follower.steer_turn("thread-1", "Prefer the TypeScript UI path")
    interrupt_response = await follower.interrupt_turn("thread-1", "user changed scope")
    compact_response = await follower.compact_thread("thread-1", token_usage_ratio=0.9)

    assert steer_response["accepted"] is True
    assert interrupt_response["interrupted"] is True
    assert compact_response["compacted"] is True
    assert compact_response["summary"]
    assert [event["type"] for event in events] == [
        "codex/event/thread_follower_start_turn",
        "codex/event/thread_follower_steer_turn",
        "codex/event/thread_follower_interrupt_turn",
        "codex/event/thread_follower_compact_thread",
    ]


@pytest.mark.asyncio
async def test_start_turn_keeps_codex_runtime_settings():
    follower = ThreadFollower()
    response = await follower.start_turn(
        thread_id="thread-2",
        session_id="session-2",
        message="Implement approval UI",
        settings=ThreadSettings(
            model="deepseek-v4-flash",
            reasoning_effort="xhigh",
            sandbox_policy="workspace-write",
            approval_mode="on-request",
        ),
    )

    assert response["thread_id"] == "thread-2"
    assert response["settings"] == {
        "model": "deepseek-v4-flash",
        "reasoning_effort": "xhigh",
        "sandbox_policy": "workspace-write",
        "approval_mode": "on-request",
    }


@pytest.mark.asyncio
async def test_settings_and_followups_emit_frontend_callable_events():
    events = []

    async def emit(event):
        events.append(event.to_dict())

    follower = ThreadFollower(event_emit=emit)
    await follower.start_turn("thread-3", "session-3", "Keep UI style unchanged")

    settings_response = await follower.update_thread_settings(
        "thread-3",
        ThreadSettings(model="gpt-5.5", reasoning_effort="medium", sandbox_policy="workspace-write"),
    )
    followups_response = await follower.set_queued_followups(
        "thread-3",
        ["add API control bridge", "verify desktop build"],
    )
    compact_response = await follower.compact_thread("thread-3", token_usage_ratio=0.4)

    assert settings_response["settings"]["model"] == "gpt-5.5"
    assert followups_response["queued_followups"] == ["add API control bridge", "verify desktop build"]
    assert compact_response == {"thread_id": "thread-3", "compacted": False, "summary": ""}
    assert [event["type"] for event in events] == [
        "codex/event/thread_follower_start_turn",
        "codex/event/thread_follower_update_thread_settings",
        "codex/event/thread_follower_set_queued_followups_state",
        "codex/event/thread_follower_compact_thread",
    ]
