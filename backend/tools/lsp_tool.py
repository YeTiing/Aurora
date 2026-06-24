# -*- coding: utf-8 -*-
"""LSP Tool — Agent-callable LSP queries (hover, definition, references, diagnostics).

Port of cc-haha's src/tools/LSPTool.
Wraps backend.lsp manager into tool spec for agent to call directly.
"""

from __future__ import annotations
import asyncio, logging
from typing import Any

logger = logging.getLogger("aurora.tools.lsp")

LSP_TOOL_SPEC = {
    "name": "lsp",
    "description": "Query language server for type info, go-to-definition, references, or diagnostics. Use after writing code to check for errors, or to understand unfamiliar code.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["hover", "definition", "references", "diagnostics"], "description": "What to query: hover (type info), definition (where defined), references (where used), diagnostics (errors/warnings)"},
            "filepath": {"type": "string", "description": "Absolute or workspace-relative file path"},
            "line": {"type": "integer", "description": "1-based line number (for hover/definition/references)"},
            "character": {"type": "integer", "description": "1-based character column (for hover/definition/references)"},
        },
        "required": ["action", "filepath"],
    },
}


async def lsp_handler(arguments: dict, workspace: str = ".") -> dict:
    """Handle LSP tool calls from the agent."""
    action = arguments.get("action", "diagnostics")
    filepath = arguments.get("filepath", "")
    line = arguments.get("line", 1)
    character = arguments.get("character", 1)

    if not filepath:
        return {"success": False, "error": "filepath is required"}

    # Resolve relative paths
    import os as _os
    if not _os.path.isabs(filepath):
        filepath = _os.path.join(workspace, filepath)
    if not _os.path.isfile(filepath):
        return {"success": False, "error": f"File not found: {filepath}"}

    try:
        from backend.lsp import get_manager, get_registry

        mgr = await get_manager()
        if not mgr or not mgr.is_ready:
            available = []
            try:
                from backend.lsp.config import find_available_servers
                available = list(find_available_servers().keys())
            except Exception:
                pass
            hint = f" (available: {available})" if available else " (no LSP servers found on PATH)"
            return {"success": False, "error": f"LSP not initialized{hint}. Install a language server: pip install pyright; npm i -g typescript-language-server"}

        # Notify file open if not already
        if not mgr.is_file_open(filepath):
            try:
                content = open(filepath, encoding="utf-8", errors="replace").read()
                await mgr.open_file(filepath, content)
            except Exception:
                pass

        if action == "hover":
            result = await mgr.get_hover(filepath, line - 1, character - 1)
            if result:
                contents = result.get("contents", {})
                if isinstance(contents, dict):
                    text = contents.get("value", str(contents))
                elif isinstance(contents, list):
                    text = "\n".join(
                        c.get("value", str(c)) if isinstance(c, dict) else str(c)
                        for c in contents
                    )
                else:
                    text = str(contents)
                return {
                    "success": True,
                    "action": "hover",
                    "filepath": filepath,
                    "line": line,
                    "character": character,
                    "result": text[:2000],
                    "range": result.get("range"),
                }
            return {"success": True, "action": "hover", "result": None, "detail": "No hover info available"}

        elif action == "definition":
            result = await mgr.get_definition(filepath, line - 1, character - 1)
            if result:
                locations = result if isinstance(result, list) else [result]
                formatted = []
                for loc in locations[:10]:
                    uri = loc.get("uri", loc.get("targetUri", ""))
                    rng = loc.get("range", {})
                    start = rng.get("start", {})
                    formatted.append({
                        "file": uri.replace("file:///", "").replace("file://", ""),
                        "line": start.get("line", 0) + 1,
                        "character": start.get("character", 0) + 1,
                    })
                return {"success": True, "action": "definition", "locations": formatted, "count": len(formatted)}
            return {"success": True, "action": "definition", "result": None, "detail": "No definition found"}

        elif action == "references":
            result = await mgr.get_references(filepath, line - 1, character - 1)
            if result:
                locations = result if isinstance(result, list) else []
                formatted = []
                for loc in locations[:20]:
                    uri = loc.get("uri", "")
                    rng = loc.get("range", {})
                    start = rng.get("start", {})
                    formatted.append({
                        "file": uri.replace("file:///", "").replace("file://", ""),
                        "line": start.get("line", 0) + 1,
                        "character": start.get("character", 0) + 1,
                    })
                return {"success": True, "action": "references", "locations": formatted, "count": len(formatted)}
            return {"success": True, "action": "references", "result": None, "detail": "No references found"}

        elif action == "diagnostics":
            # Get diagnostics from pull model or pending registry
            diags = await mgr.get_diagnostics(filepath)
            if not diags:
                registry = get_registry()
                pending = registry.get_attachments()
                diags = [d for d in pending if d.get("_uri", "") == filepath or d.get("filepath", "") == filepath]

            errors = [d for d in diags if d.get("severity") in ("Error", 1, "error")]
            warnings = [d for d in diags if d.get("severity") in ("Warning", 2, "warning")]
            infos = [d for d in diags if d.get("severity") in ("Info", 3, "info", "Hint", 4, "hint")]

            lines = []
            for d in errors[:8]:
                lines.append(f"ERROR L{d.get('line',0)+1}: {d.get('message','')}")
            for d in warnings[:5]:
                lines.append(f"WARN L{d.get('line',0)+1}: {d.get('message','')}")

            return {
                "success": True,
                "action": "diagnostics",
                "filepath": filepath,
                "error_count": len(errors),
                "warning_count": len(warnings),
                "info_count": len(infos),
                "summary": "\n".join(lines) if lines else "No diagnostics",
                "all": diags[:30],
            }

    except ImportError:
        return {"success": False, "error": "LSP module not available"}
    except Exception as e:
        logger.debug(f"LSP tool error: {e}")
        return {"success": False, "error": str(e)[:500]}
