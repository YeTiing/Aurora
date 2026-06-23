# todo_write — 任务追踪工具，Agent执行过程中记录待办项
from __future__ import annotations
import json, time
from typing import Any
from .base import ToolSpec, ToolCallResult

TODO_SPEC = ToolSpec(
    name="todo_write",
    description="Create and manage a structured task list for your current coding session. Use this to track progress, organize complex tasks, and demonstrate thoroughness.",
    parameters={
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated task list. Max 20 items.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique identifier (e.g., '1', '2')"},
                        "content": {"type": "string", "description": "Task description"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["id", "content", "status"]
                }
            }
        },
        "required": ["todos"]
    },
    category="task_management",
    exposure="direct",
)

_todos: dict[str, list[dict]] = {}


async def todo_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    todos = arguments.get("todos", [])
    if len(todos) > 20:
        todos = todos[:20]

    _todos[workspace] = todos

    status_icons = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "cancelled": "[-]"}
    lines = ["## Task List"]
    done = sum(1 for t in todos if t["status"] == "completed")
    total = len(todos)
    if total > 0:
        lines.append(f"Progress: {done}/{total} ({int(done/total*100)}%)\n")

    for t in todos:
        icon = status_icons.get(t.get("status", "pending"), "[ ]")
        priority = f"[{t.get('priority', 'medium')}]" if t.get("priority") else ""
        lines.append(f"- {icon} {priority} {t['content']}")

    return "\n".join(lines)


def get_current_todos(workspace: str = ".") -> list[dict]:
    return _todos.get(workspace, [])


# ── plan_update ──
PLAN_UPDATE_SPEC = ToolSpec(
    name="plan_update",
    description="Update the current execution plan during an active task. Use when progress changes, new steps are needed, or the plan needs adjustment.",
    parameters={
        "type": "object",
        "properties": {
            "step_id": {"type": "integer", "description": "Step number to update"},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "failed", "skipped"]},
            "notes": {"type": "string", "description": "Notes about the step's status change"},
            "new_steps": {
                "type": "array",
                "description": "New steps to insert after the current step",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "tool": {"type": "string"},
                    },
                    "required": ["description"]
                }
            }
        },
        "required": ["step_id", "status"]
    },
    category="task_management",
    exposure="direct",
)


async def plan_update_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    step_id = arguments.get("step_id", 0)
    status = arguments.get("status", "completed")
    notes = arguments.get("notes", "")
    new_steps = arguments.get("new_steps", [])

    lines = [f"Step {step_id}: {status}"]
    if notes:
        lines.append(f"  Note: {notes}")
    if new_steps:
        lines.append(f"  Added {len(new_steps)} new steps")
    return "\n".join(lines)