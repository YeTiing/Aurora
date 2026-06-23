"""Doc Ghost tool — proactive documentation generation."""
import json
from backend.tools.base import ToolSpec, ToolCallResult

async def doc_ghost_handler(action: str = "", file: str = "", files: str = "", id: str = "") -> ToolCallResult:
    try:
        from backend.doc_ghost import get_doc_ghost
        g = get_doc_ghost()

        if action == "scan":
            changes = g.scan_changes()
            snap = g.detect_feature_completion(changes)
            result = {"changes": len(changes), "feature_detected": snap is not None}
            if snap:
                result["summary"] = snap.summary
                result["suggestion"] = snap.doc_suggestion
                result["snapshot_id"] = snap.id
                result["files"] = [c.path for c in snap.files[:10]]
            return ToolCallResult(success=True, output=json.dumps(result, indent=2, ensure_ascii=False))

        elif action == "pending":
            pending = g.get_pending()
            if not pending:
                return ToolCallResult(success=True, output="No pending doc suggestions.")
            return ToolCallResult(success=True, output=json.dumps(pending, indent=2, ensure_ascii=False))

        elif action == "generate-api-doc":
            if not file:
                return ToolCallResult(success=False, output="", error="'file' required")
            return ToolCallResult(success=True, output=g.generate_api_doc(file))

        elif action == "generate-changelog":
            changes = g.scan_changes()
            if not changes:
                return ToolCallResult(success=True, output="No recent changes detected.")
            changelog = g.generate_changelog(changes)
            return ToolCallResult(success=True, output=changelog)

        elif action == "dismiss":
            if not id:
                return ToolCallResult(success=False, output="", error="'id' required")
            g.dismiss(id)
            return ToolCallResult(success=True, output=f"Suggestion {id} dismissed.")

        elif action == "stats":
            return ToolCallResult(success=True, output=json.dumps(g.stats(), indent=2, ensure_ascii=False))

        else:
            return ToolCallResult(success=False, output="", error=f"Unknown action: {action}")

    except Exception as e:
        return ToolCallResult(success=False, output="", error=f"{type(e).__name__}: {str(e)[:300]}")

DOC_GHOST_SPEC = ToolSpec(
    name="doc_ghost",
    description="Proactive documentation: scan workspace for completed features, generate API docs, changelog entries. Actions: scan, pending, generate-api-doc, generate-changelog, dismiss, stats.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["scan", "pending", "generate-api-doc", "generate-changelog", "dismiss", "stats"]},
            "file": {"type": "string", "description": "File path for doc generation"},
            "files": {"type": "string", "description": "Comma-separated file paths"},
            "id": {"type": "string", "description": "Suggestion ID for dismiss"},
        },
        "required": ["action"],
    },
)
