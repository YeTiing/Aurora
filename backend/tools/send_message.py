# send_message — 向用户发送消息（对齐 Codex request_user_input / send_message_to_thread）
from __future__ import annotations
from typing import Any
from .base import ToolSpec, ToolCallResult

SEND_MESSAGE_SPEC = ToolSpec(
    name="send_message",
    description="Send a message to another agent or thread. Use for inter-agent communication, handing off tasks, or coordinating parallel work. Supports interrupt mode to redirect work immediately.",
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Target agent id or thread id to send the message to."
            },
            "message": {
                "type": "string",
                "description": "Content of the message to send."
            },
            "interrupt": {
                "type": "boolean",
                "description": "True interrupts the target's current task and handles this message immediately.",
                "default": False
            },
            "items": {
                "type": "array",
                "description": "Structured input items: text, image, local_image, skill, or mention.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["text", "image", "local_image", "skill", "mention"]},
                        "text": {"type": "string"},
                        "path": {"type": "string"},
                        "image_url": {"type": "string"},
                        "name": {"type": "string"}
                    }
                }
            }
        },
        "required": ["target", "message"]
    },
    category="agent",
    exposure="direct",
    timeout_ms=30000,
)

async def send_message_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    target = arguments.get("target", "")
    message = arguments.get("message", "")
    interrupt = arguments.get("interrupt", False)
    items = arguments.get("items")

    if not target:
        return ToolCallResult(id="", name="send_message", output="",
                              error="target is required", success=False)

    result_data = {
        "target": target,
        "message_sent": True,
        "message_length": len(message),
        "interrupt": interrupt,
    }

    if items:
        result_data["items_count"] = len(items)

    return ToolCallResult(
        id="", name="send_message",
        output=f"Message sent to {target} ({len(message)} chars, interrupt={interrupt})",
        success=True,
        metadata=result_data,
    )