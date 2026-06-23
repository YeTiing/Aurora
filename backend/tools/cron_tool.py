import json

"""Cron tool — schedule recurring tasks with natural language."""
from backend.cron_scheduler import get_cron
from backend.tools.base import ToolSpec, ToolCallResult


async def cron_handler(
    action: str = "",
    name: str = "",
    schedule: str = "",
    prompt: str = "",
) -> ToolCallResult:
    cron = get_cron()

    try:
        if action == "add":
            if not name or not schedule or not prompt:
                return ToolCallResult(success=False, output="", error="'name', 'schedule', and 'prompt' required")
            ok, msg = cron.add(name, schedule, prompt)
            return ToolCallResult(success=ok, output=msg)

        elif action == "remove":
            if not name:
                return ToolCallResult(success=False, output="", error="'name' required")
            ok = cron.remove(name)
            return ToolCallResult(success=ok, output="Removed." if ok else f"Not found: {name}")

        elif action == "toggle":
            enabled = cron.toggle(name)
            return ToolCallResult(success=True, output=f"Task '{name}' {'enabled' if enabled else 'disabled'}.")

        elif action == "list":
            tasks = cron.list_tasks()
            if not tasks:
                return ToolCallResult(success=True, output="No cron tasks.")
            lines = ["Cron tasks:", ""]
            for t in tasks:
                status = "▶" if t["enabled"] else "⏸"
                lines.append(f"  {status} [{t['name']}] {t['schedule']} (runs={t['run_count']})")
            return ToolCallResult(success=True, output='\n'.join(lines))

        elif action == "stats":
            return ToolCallResult(success=True, output=json.dumps(cron.stats(), indent=2, ensure_ascii=False))

        else:
            return ToolCallResult(success=False, output="", error=f"Unknown action: {action}")

    except Exception as e:
        return ToolCallResult(success=False, output="", error=str(e))


CRON_SPEC = ToolSpec(
    name="cron",
    description=(
        "Schedule recurring tasks with natural language. "
        "Schedule examples: 'every 10 minutes', 'every 1 hour', 'daily at 08:00', 'daily at 18:30'. "
        "When a task fires, it appears as a system message in the conversation. "
        "Actions: add, remove, toggle, list, stats."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "remove", "toggle", "list", "stats"]},
            "name": {"type": "string", "description": "Task name (e.g., 'morning-standup-reminder')"},
            "schedule": {"type": "string", "description": "Natural schedule: 'every N minutes', 'daily at HH:MM'"},
            "prompt": {"type": "string", "description": "What to inject when the task fires"},
        },
        "required": ["action"],
    },
    category="automation",
)
