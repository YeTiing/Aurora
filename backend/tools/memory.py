import json

"""Memory tool — agent-managed dual-file memory system."""
from backend.dual_memory import get_dual_memory, MEMORY_DIR, MAX_AGENT_MEMORY_CHARS, MAX_USER_PROFILE_CHARS
from backend.tools.base import ToolSpec, ToolCallResult


async def memory_handler(
    action: str = "",
    store: str = "agent",
    text: str = "",
    index: int = -1,
) -> ToolCallResult:
    """Memory tool handler.

    Actions:
        add     — add a new entry to the memory store
        replace — replace an existing entry
        remove  — delete an entry
        list    — show all entries
        search  — search past sessions
        stats   — show memory statistics

    Stores:
        agent   — AGENT_MEMORY.md (facts, conventions, environment)
        user    — USER_PROFILE.md (preferences, style, habits)
    """
    dm = get_dual_memory()
    store_obj = dm.agent_memory if store == "agent" else dm.user_profile

    try:
        if action == "add":
            if not text:
                return ToolCallResult(success=False, output="", error="'text' is required for add")
            ok, msg = store_obj.add(text, source="agent")
            return ToolCallResult(success=ok, output=msg)

        elif action == "replace":
            if index < 0 or not text:
                return ToolCallResult(success=False, output="", error="'index' and 'text' are required for replace")
            ok, msg = store_obj.replace(index, text, source="agent")
            return ToolCallResult(success=ok, output=msg)

        elif action == "remove":
            if index < 0:
                return ToolCallResult(success=False, output="", error="'index' is required for remove")
            ok, msg = store_obj.remove(index)
            return ToolCallResult(success=ok, output=msg)

        elif action == "list":
            entries = store_obj.list_entries()
            if not entries:
                return ToolCallResult(success=True, output=f"{store_obj.name}: (empty)")
            lines = [
                f"{store_obj.name} [{store_obj.char_count}/{store_obj.max_chars} chars — {store_obj.usage_pct}%]:",
                "",
            ]
            for e in entries:
                lines.append(f"  [{e['index']}] {e['text']}  (source: {e['source']}, {e['length']} chars)")
            return ToolCallResult(success=True, output='\n'.join(lines))

        elif action == "search":
            results = dm.search_past_sessions(text or action)
            if not results:
                return ToolCallResult(success=True, output="No relevant past sessions found.")
            lines = ["Past sessions:", ""]
            for r in results:
                lines.append(f"  [{r['session_id'][:8]}] {r['content'][:200]}")
            return ToolCallResult(success=True, output='\n'.join(lines))

        elif action == "stats":
            stats = dm.stats()
            return ToolCallResult(success=True, output=json.dumps(stats, indent=2, ensure_ascii=False))

        else:
            return ToolCallResult(success=False, output="", error=f"Unknown action: {action}")

    except Exception as e:
        return ToolCallResult(success=False, output="", error=str(e))


MEMORY_SPEC = ToolSpec(
    name="memory",
    description=(
        "Manage Aurora's persistent memory across sessions. "
        "Use to remember facts about the user, project conventions, "
        "environment details, and things learned. "
        "Two stores: 'agent' (facts/notes, 2200 char limit) and "
        "'user' (preferences/style, 1375 char limit). "
        "Actions: add, replace, remove, list, search, stats."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "list", "search", "stats"],
            },
            "store": {
                "type": "string",
                "enum": ["agent", "user"],
                "default": "agent",
            },
            "text": {
                "type": "string",
            },
            "index": {
                "type": "integer",
            },
        },
        "required": ["action"],
    },
    category="memory",
)
