"""Skin tool — switch themes, preview CSS, manage skins."""

import json
from backend.tools.base import ToolSpec, ToolCallResult


async def skin_handler(
    action: str = "",
    name: str = "",
    data: str = "",
) -> ToolCallResult:
    """Skin management tool. actions: list, get, apply, preview, create, delete, export, import"""
    from backend.skin_engine import get_skin_manager

    mgr = get_skin_manager()

    try:
        if action == "list":
            skins = mgr.list_skins()
            active = mgr.get_active_skin()
            lines = ["Available skins:", ""]
            for s in skins:
                marker = " *" if s["name"] == active["name"] else "  "
                lines.append(f"{marker} {s['name']} — {s['label']} ({s['accent_color_name']})")
            return ToolCallResult(success=True, output='\n'.join(lines))

        elif action == "get":
            if not name:
                name = mgr.get_active_skin()["name"]
            skin = mgr.get_skin(name)
            if not skin:
                return ToolCallResult(success=False, output="", error=f"Skin '{name}' not found")
            return ToolCallResult(success=True, output=json.dumps(skin.to_dict(), indent=2, ensure_ascii=False))

        elif action == "apply":
            if not name:
                return ToolCallResult(success=False, output="", error="'name' required")
            ok = mgr.apply_skin(name)
            if not ok:
                return ToolCallResult(success=False, output="", error=f"Skin '{name}' not found")
            return ToolCallResult(success=True, output=f"Theme switched to '{name}'.")

        elif action == "preview":
            if not name:
                return ToolCallResult(success=False, output="", error="'name' required")
            css = mgr.preview_css(name)
            if not css:
                return ToolCallResult(success=False, output="", error=f"Skin '{name}' not found")
            return ToolCallResult(success=True, output=css)

        elif action == "create":
            if not name:
                return ToolCallResult(success=False, output="", error="'name' required")
            skin_data = {}
            if data:
                try:
                    skin_data = json.loads(data)
                except json.JSONDecodeError:
                    return ToolCallResult(success=False, output="", error="Invalid JSON in 'data'")
            skin = mgr.save_skin(name, skin_data)
            return ToolCallResult(success=True, output=f"Skin '{name}' created.")

        elif action == "delete":
            if not name:
                return ToolCallResult(success=False, output="", error="'name' required")
            ok = mgr.delete_skin(name)
            if not ok:
                return ToolCallResult(success=False, output="", error=f"Cannot delete '{name}' (built-in or not found)")
            return ToolCallResult(success=True, output=f"Skin '{name}' deleted.")

        elif action == "export":
            if not name:
                return ToolCallResult(success=False, output="", error="'name' required")
            export = mgr.export_skin(name)
            if not export:
                return ToolCallResult(success=False, output="", error=f"Skin '{name}' not found")
            return ToolCallResult(success=True, output=json.dumps(export, indent=2, ensure_ascii=False))

        elif action == "import":
            if not data:
                return ToolCallResult(success=False, output="", error="'data' required (JSON skin export)")
            try:
                skin_data = json.loads(data)
            except json.JSONDecodeError:
                return ToolCallResult(success=False, output="", error="Invalid JSON in 'data'")
            skin = mgr.import_skin(skin_data)
            return ToolCallResult(success=True, output=f"Skin '{skin.name}' imported.")

        else:
            return ToolCallResult(success=False, output="", error=f"Unknown action: {action}. Use: list, get, apply, preview, create, delete, export, import")

    except Exception as e:
        return ToolCallResult(success=False, output="", error=str(e))


SKIN_SPEC = ToolSpec(
    name="skin",
    description=(
        "Manage Aurora themes/skins. "
        "Actions: list (show all), get (show details), apply (switch theme), "
        "preview (show CSS vars), create, delete, export, import. "
        "Built-in themes: aurora-dark, aurora-light, midnight-purple, forest-green, "
        "ocean-blue, sunset-orange, rose-dawn, monochrome."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: list, get, apply, preview, create, delete, export, import",
                "enum": ["list", "get", "apply", "preview", "create", "delete", "export", "import"],
            },
            "name": {"type": "string", "description": "Skin name"},
            "data": {"type": "string", "description": "JSON data for create/import"},
        },
        "required": ["action"],
    },
)
