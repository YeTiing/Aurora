"""Session export tool - export conversations as Markdown/JSON."""
import json
from backend.tools.base import ToolSpec, ToolCallResult

async def session_export_handler(action: str = "", session_data: str = "", format: str = "md") -> ToolCallResult:
    try:
        from backend.session_export import export_session, export_session_json, ExportConfig

        if action == "export":
            if not session_data:
                return ToolCallResult(success=False, output="", error="'session_data' required - pass the session JSON")
            try: data = json.loads(session_data)
            except: return ToolCallResult(success=False, output="", error="Invalid JSON in session_data")
            config = ExportConfig()
            md = export_session(data, config)
            p = None
            try:
                from pathlib import Path
                title = data.get("title", "session").replace(" ", "_")[:50]
                slug = "".join(c for c in title if c.isalnum() or c in "_-")
                p = Path.cwd() / f"{slug}.md"
                p.write_text(md, encoding="utf-8")
            except: pass
            preview = md[:2000] + ("\n\n... (full file saved)" if p else "")
            return ToolCallResult(success=True, output=preview, metadata={"file": str(p) if p else None})

        return ToolCallResult(success=False, output="", error=f"Unknown action: {action}")
    except Exception as e:
        return ToolCallResult(success=False, output="", error=str(e)[:300])

SESSION_EXPORT_SPEC = ToolSpec(
    name="session_export",
    description="Export current session conversation to Markdown. Pass action='export' and session_data (JSON of the session).",
    parameters={"type":"object","properties":{"action":{"type":"string","enum":["export"]},"session_data":{"type":"string","description":"JSON serialized session data"},"format":{"type":"string","enum":["md","json"]}},"required":["action","session_data"]},
)
