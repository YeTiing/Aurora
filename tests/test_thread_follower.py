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
    assert follower.get_thread("thread-2").queued_followups == []
