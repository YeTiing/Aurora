import json

"""Memory tool — full closed-loop memory: dual-file + skills + nudge + search."""
from backend.dual_memory import get_closed_loop
from backend.tools.base import ToolSpec, ToolCallResult


async def memory_handler(
    action: str = "",
    store: str = "agent",
    text: str = "",
    index: int = -1,
) -> ToolCallResult:
    cl = get_closed_loop()

    try:
        # ── Dual-file actions ──
        if action == "add":
            if not text: return ToolCallResult(success=False, output="", error="'text' is required")
            s = cl.user_profile if store == "user" else cl.agent_memory
            ok, msg = s.add(text, source="agent")
            return ToolCallResult(success=ok, output=msg)

        elif action == "replace":
            if index < 0 or not text: return ToolCallResult(success=False, output="", error="'index' and 'text' required")
            s = cl.user_profile if store == "user" else cl.agent_memory
            ok, msg = s.replace(index, text, source="agent")
            return ToolCallResult(success=ok, output=msg)

        elif action == "remove":
            if index < 0: return ToolCallResult(success=False, output="", error="'index' required")
            s = cl.user_profile if store == "user" else cl.agent_memory
            ok, msg = s.remove(index)
            return ToolCallResult(success=ok, output=msg)

        elif action == "list":
            s = cl.user_profile if store == "user" else cl.agent_memory
            entries = s.list_entries()
            if not entries: return ToolCallResult(success=True, output=f"{s.name}: (empty)")
            lines = [f"{s.name} [{s.char_count}/{s.max_chars} — {s.usage_pct}%]:", ""]
            for e in entries:
                lines.append(f"  [{e['index']}] {e['text']}  ({e['length']} chars)")
            return ToolCallResult(success=True, output='\n'.join(lines))

        # ── Skill actions ──
        elif action == "skill_create":
            if not text: return ToolCallResult(success=False, output="", error="'text' (description) required")
            p = cl.skills.create(
                name=store if store != "agent" else "learned-skill",
                desc=text[:200],
                body=text if len(text) > 200 else text + "\n\nAuto-extracted by Aurora.",
                source="agent"
            )
            return ToolCallResult(success=True, output=f"Skill created: {p.stem}")

        elif action == "skill_patch":
            if not text or store == "agent": return ToolCallResult(success=False, output="", error="store=skill_name, text=new body")
            ok = cl.skills.patch(store, text)
            return ToolCallResult(success=ok, output="Skill patched." if ok else f"Skill not found: {store}")

        elif action == "skill_use":
            cl.skills.use(store if store != "agent" else "")
            return ToolCallResult(success=True, output=f"Recorded use of: {store}")

        elif action == "skill_list":
            skills = cl.skills.all()
            if not skills: return ToolCallResult(success=True, output="No skills.")
            active = [s for s in skills if s.get("state") != "archived"]
            archived = [s for s in skills if s.get("state") == "archived"]
            lines = [f"Skills: {len(active)} active, {len(archived)} archived", ""]
            for s in active:
                lines.append(f"  [{s['state']}] {s['name']} (uses={s.get('uses',0)}, source={s.get('source','agent')})")
            if archived:
                lines.append("", "Archived:")
                for s in archived[:5]:
                    lines.append(f"  [archive] {s['name']}")
            return ToolCallResult(success=True, output='\n'.join(lines))

        # ── Search ──
        elif action == "search":
            results = cl.fts5.search(text or "", limit=5)
            if not results: return ToolCallResult(success=True, output="No past sessions found.")
            lines = ["Past sessions:", ""]
            for r in results:
                lines.append(f"  [{r.get('sid',r.get('session_id','?'))[:8]}] {r.get('summary','')[:200]}")
            return ToolCallResult(success=True, output='\n'.join(lines))

        # ── Nudge ──
        elif action == "nudge_check":
            if cl.nudge.should():
                return ToolCallResult(success=True, output=cl.nudge.prompt())
            return ToolCallResult(success=True, output="No nudge needed.")

        elif action == "honcho_status":
            ctx = cl.honcho.prompt_injection()
            return ToolCallResult(success=True, output=ctx or "No user model yet.")

        # ── Stats ──
        elif action == "stats":
            return ToolCallResult(success=True, output=json.dumps(cl.stats(), indent=2, ensure_ascii=False))

        else:
            return ToolCallResult(success=False, output="", error=f"Unknown action: {action}")

    except Exception as e:
        return ToolCallResult(success=False, output="", error=str(e))


MEMORY_SPEC = ToolSpec(
    name="memory",
    description=(
        "Manage Aurora's persistent closed-loop memory across sessions. "
        "Dual-file memory: 'agent' (AGENT_MEMORY.md, 2200 chars) and 'user' (USER_PROFILE.md, 1375 chars). "
        "Skills: create reusable knowledge documents that self-improve. "
        "Actions: add, replace, remove, list, skill_create, skill_patch, skill_use, skill_list, "
        "search (past sessions via FTS5), nudge_check, honcho_status, stats."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [
                "add","replace","remove","list",
                "skill_create","skill_patch","skill_use","skill_list",
                "search","nudge_check","honcho_status","stats"
            ]},
            "store": {"type": "string", "description": "Target store: agent/user for memory ops, or skill name for skill ops"},
            "text": {"type": "string", "description": "Entry text, skill body, or search query"},
            "index": {"type": "integer", "description": "Entry index for replace/remove"},
        },
        "required": ["action"],
    },
    category="memory",
)
