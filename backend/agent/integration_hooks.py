# -*- coding: utf-8 -*-
"""Agent Integration Hooks — wire LSP, AutoDream, Bash Classifier into agent pipeline.

These hooks connect previously standalone modules into the actual agent lifecycle:
  - post_file_edit: After apply_patch/file_rw, inject LSP diagnostics
  - post_session: After synthesizer completes, trigger AutoDream consolidation
  - pre_shell_exec: Before shell_command, run bash safety classification
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional, Callable

logger = logging.getLogger("aurora.hooks")

# ── Hook 1: Post-File-Edit LSP Diagnostic Injection ────────────

_FILE_MODIFYING_TOOLS = {"apply_patch", "file_rw"}


async def post_file_edit_hook(
    tool_name: str,
    arguments: dict,
    result: dict,
    lsp_manager=None,
) -> Optional[str]:
    """After a file-modifying tool executes, fetch LSP diagnostics.

    Returns a diagnostic string to inject into conversation, or None.
    """
    if tool_name not in _FILE_MODIFYING_TOOLS:
        return None

    if not result.get("success", False):
        return None

    # Extract filepath from tool arguments
    filepath = _extract_filepath(tool_name, arguments)
    if not filepath or not os.path.isfile(filepath):
        return None

    # Skip non-code files
    _, ext = os.path.splitext(filepath)
    code_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".c", ".cpp",
                 ".h", ".hpp", ".java", ".kt", ".swift", ".rb", ".php", ".cs", ".lua"}
    if ext.lower() not in code_exts:
        return None

    try:
        from backend.lsp import get_manager

        mgr = await get_manager() if lsp_manager is None else lsp_manager
        if mgr is None or not mgr.is_ready:
            return None

        # Notify LSP of file change
        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="replace")
            await mgr.change_file(filepath, content)
        except Exception:
            pass

        # Get diagnostics
        diags = await mgr.get_diagnostics(filepath)
        if not diags:
            return None

        # Format for injection
        errors = [d for d in diags if d.get("severity") == "Error"]
        warnings = [d for d in diags if d.get("severity") == "Warning"]

        if not errors and not warnings:
            return None

        lines = [f"\n[LSP Diagnostics for {filepath}]"]
        for d in errors[:5]:
            lines.append(f"  ERROR L{d.get('line',0)+1}: {d.get('message','')}")
        for d in warnings[:3]:
            lines.append(f"  WARN L{d.get('line',0)+1}: {d.get('message','')}")

        return "\n".join(lines)

    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"LSP post-edit hook error: {e}")
        return None


def _extract_filepath(tool_name: str, arguments: dict) -> Optional[str]:
    """Extract target filepath from tool arguments."""
    if tool_name == "apply_patch":
        return arguments.get("file_path") or arguments.get("path")
    if tool_name == "file_rw":
        return arguments.get("path") or arguments.get("file_path")
    return arguments.get("path") or arguments.get("filepath")


# ── Hook 2: Post-Session AutoDream Consolidation ───────────────

async def post_session_hook(
    session_id: str = "",
    memory_dir: str = ".aurora/memory",
    transcript_dir: str = ".aurora/sessions",
    dispatch_fn: Callable | None = None,
) -> dict:
    """After agent session completes, try AutoDream consolidation.

    Returns: {"fired": bool, "reason": str}
    """
    try:
        from backend.auto_dream import AutoDream, AutoDreamConfig

        config = AutoDreamConfig.from_env()
        dream = AutoDream(memory_dir, transcript_dir, config, dispatch_fn)

        passes, reason = dream.all_gates_pass()
        if not passes:
            logger.debug(f"AutoDream skip: {reason}")
            return {"fired": False, "reason": reason}

        result = await dream.try_consolidate()
        return result

    except ImportError:
        return {"fired": False, "reason": "module not available"}
    except Exception as e:
        logger.error(f"AutoDream hook error: {e}")
        return {"fired": False, "reason": str(e)}


# ── Hook 3: Pre-Shell Bash Safety Check ────────────────────────

def pre_shell_exec_hook(command: str, approval_policy: str = "on-request") -> dict:
    """Before executing a shell command, classify and check safety.

    Returns: {"allowed": bool, "risk": str, "reason": str, "requires_approval": bool}
    """
    try:
        from backend.bash_classifier import get_classifier

        classifier = get_classifier()
        cls = classifier.classify_pipeline(command)
        allowed, reason = classifier.approve_for_policy(command, approval_policy)

        return {
            "allowed": allowed,
            "risk": cls.risk.value,
            "reason": reason or cls.reason,
            "requires_approval": cls.requires_approval,
            "matched_pattern": cls.matched_pattern,
        }
    except ImportError:
        return {"allowed": True, "risk": "unknown", "reason": "classifier unavailable"}
    except Exception as e:
        return {"allowed": True, "risk": "error", "reason": str(e)}

# ── Hook 4: Post-File-Edit Security Scan ──────────────────────

async def post_edit_security_hook(filepath: str) -> Optional[str]:
    """After file edit, run quick secrets scan. Returns warning or None."""
    try:
        from backend.security_scanner import get_scanner
        scanner = get_scanner()
        return await scanner.post_edit_scan(filepath)
    except ImportError:
        return None
    except Exception:
        return None
