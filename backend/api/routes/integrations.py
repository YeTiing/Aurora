"""Aurora API - Integrated routes for MagicDocs, IM, Worktree, Session Search."""
from __future__ import annotations
import asyncio, json, time
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, Optional

router = APIRouter()

from backend.api.deps import (
    get_config as _get_cfg, get_llm as _get_llm,
)

_cfg = None; _llm = None
def _init_cfg():
    global _cfg; _cfg = _get_cfg()
def _init_llm():
    global _llm; _llm = _get_llm()
def _init():
    _init_cfg()


# ====== MagicDocs ======

@router.get("/magic-docs")
async def list_magic_docs():
    from backend.magic_docs import magic_docs_manager
    return {"magic_docs": magic_docs_manager.list_all()}

@router.post("/magic-docs/register")
async def register_magic_doc(req: dict):
    from backend.magic_docs import magic_docs_manager
    magic_docs_manager.register(req["path"], req.get("content", ""))
    return {"registered": True, "tracked": magic_docs_manager.tracked_count}

@router.post("/magic-docs/{path:path}/update")
async def update_magic_doc(path: str, req: dict):
    from backend.magic_docs import magic_docs_manager
    _init_llm()
    conversation = req.get("conversation", "")
    result = await magic_docs_manager.run_update(
        "/" + path, conversation, _llm, None
    )
    return {"path": path, "result": result[:500]}


# ====== IM Adapter Bridge ======

@router.get("/im/stats")
async def im_stats():
    from backend.im_adapter import adapter_bridge
    return adapter_bridge.stats()

@router.post("/im/session")
async def create_im_session(req: dict):
    from backend.im_adapter import adapter_bridge
    session = adapter_bridge.create_session(
        chat_id=req["chat_id"],
        adapter_type=req.get("type", "telegram"),
        workspace=req.get("workspace", "."),
    )
    return {"session_id": session.session_id}

@router.post("/im/session/{session_id}/message")
async def im_send_message(session_id: str, req: dict):
    from backend.im_adapter import adapter_bridge
    result = await adapter_bridge.send_to_agent(
        session_id, req["text"], req.get("attachments", []),
    )
    return result

@router.post("/im/permission/{req_id}")
async def im_resolve_permission(req_id: str, req: dict):
    from backend.im_adapter import adapter_bridge
    adapter_bridge.resolve_permission(req_id, req["decision"])
    return {"resolved": True}


# ====== Session Search ======

@router.get("/search")
async def search_sessions(q: str = "", session_id: str = "", limit: int = 10):
    if not q:
        return {"hits": [], "query": ""}
    from backend.session_search import session_search
    sid = session_id if session_id else None
    hits = session_search.search(q, limit=limit, session_id=sid)
    return {
        "query": q,
        "count": len(hits),
        "hits": [
            {"session_id": h.session_id, "role": h.role,
             "snippet": h.snippet, "timestamp": h.timestamp}
            for h in hits
        ],
    }

@router.get("/search/stats")
async def search_stats():
    from backend.session_search import session_search
    return session_search.stats()

@router.post("/search/index")
async def index_message(req: dict):
    from backend.session_search import session_search
    session_search.index_message(
        session_id=req["session_id"],
        message_id=req.get("message_id", ""),
        role=req.get("role", "user"),
        content=req.get("content", ""),
    )
    return {"indexed": True}


# ====== Worktree ======

@router.get("/worktree")
async def list_worktrees():
    from backend.worktree import worktree_manager
    return {"worktrees": worktree_manager.list_all()}

@router.post("/worktree")
async def create_worktree(req: dict):
    from backend.worktree import worktree_manager
    import uuid
    info = await worktree_manager.create(
        session_id=req.get("session_id", uuid.uuid4().hex[:12]),
        repo_path=req["repo_path"],
        branch=req.get("branch"),
    )
    return {"path": info.path, "branch": info.branch, "session_id": info.session_id}

@router.delete("/worktree/{session_id}")
async def remove_worktree(session_id: str):
    from backend.worktree import worktree_manager
    await worktree_manager.remove(session_id)
    return {"removed": True}


# ====== Context Collapse ======

@router.post("/context/collapse")
async def collapse_context(req: dict):
    from backend.context.collapse import context_collapser
    messages = req.get("messages", [])
    keep_last = req.get("keep_last", 10)
    collapsed, summary = context_collapser.collapse(messages, keep_last)
    return {
        "collapsed_count": len(collapsed),
        "original_count": len(messages),
        "summary": summary[:500],
        "total_collapses": context_collapser.collapse_count,
    }

@router.get("/context/collapse/stats")
async def collapse_stats(req: dict = None):
    from backend.context.collapse import context_collapser
    return {
        "auto_compact": context_collapser.config.auto_compact,
        "max_messages": context_collapser.config.max_messages,
        "collapse_count": context_collapser.collapse_count,
    }


# ── LSP Routes ──────────────────────────────────────────────────

@router.get("/lsp/servers")
async def list_lsp_servers():
    """List available LSP servers and their status."""
    from backend.lsp import get_manager, get_builtin_configs, find_available_servers
    builtin = get_builtin_configs()
    available = find_available_servers()
    mgr = await get_manager()  # noqa
    result = {"available": {}, "unavailable": {}}
    for name, config in builtin.items():
        info = {
            "name": name,
            "command": config.command,
            "extensions": list(config.extension_to_language.keys()),
            "available": name in available,
        }
        if name in available:
            result["available"][name] = info
        else:
            result["unavailable"][name] = info
    return {"servers": result, "total_available": len(available)}

@router.post("/lsp/initialize")
async def initialize_lsp():
    """Initialize the LSP manager (auto-discovers available servers)."""
    from backend.lsp import create_server_manager
    mgr = await create_server_manager()
    return {"status": mgr.state, "servers": list(mgr.get_all_servers().keys())}

@router.get("/lsp/diagnostics")
async def get_lsp_diagnostics(filepath: str = ""):
    """Get LSP diagnostics for a file (or all pending)."""
    from backend.lsp import get_manager, get_registry
    registry = get_registry()
    if filepath:
        mgr = await get_manager()
        if mgr:
            diags = await mgr.get_diagnostics(filepath)
            return {"filepath": filepath, "diagnostics": diags}
    pending = registry.get_attachments()
    return {"pending_diagnostics": pending, "count": len(pending)}

@router.post("/lsp/hover")
async def lsp_hover(filepath: str, line: int, character: int):
    """Get hover information at a position."""
    from backend.lsp import get_manager
    mgr = await get_manager()
    if not mgr:
        return {"error": "LSP not initialized"}
    result = await mgr.get_hover(filepath, line, character)
    return {"result": result}

@router.post("/lsp/definition")
async def lsp_definition(filepath: str, line: int, character: int):
    """Get definition at a position."""
    from backend.lsp import get_manager
    mgr = await get_manager()
    if not mgr:
        return {"error": "LSP not initialized"}
    result = await mgr.get_definition(filepath, line, character)
    return {"result": result}

@router.post("/lsp/shutdown")
async def shutdown_lsp():
    """Shutdown all LSP servers."""
    from backend.lsp import get_manager
    mgr = await get_manager()
    if mgr:
        await mgr.shutdown()
    return {"status": "shutdown"}


# ── AutoDream Routes ───────────────────────────────────────────

@router.post("/dream/status")
async def get_dream_status():
    """Get AutoDream status."""
    from backend.auto_dream import create_auto_dream
    dream = create_auto_dream()
    gates_pass, reason = dream.all_gates_pass()
    last_at = dream._lock.read_last()
    return {
        "enabled": dream.is_enabled(),
        "gates_pass": gates_pass,
        "reason": reason,
        "last_consolidated_at": last_at,
        "hours_since": round((__import__("time").time() - last_at) / 3600, 1) if last_at else None,
    }

@router.post("/dream/force")
async def force_dream(extra_context: str = ""):
    """Manually trigger a dream consolidation."""
    from backend.auto_dream import create_auto_dream
    dream = create_auto_dream()
    result = await dream.force_dream(extra_context)
    return result


# ── Quality Gate Routes ────────────────────────────────────────

@router.post("/quality-gate/run")
async def run_quality_gate(quick: bool = False):
    """Run the quality gate."""
    from backend.quality_gate import QualityGate, QualityGateConfig
    config = QualityGateConfig.from_args(quick=quick)
    gate = QualityGate(config)
    report = gate.run()
    return {
        "passed": report.passed,
        "gates": report.gates,
        "warnings": report.warnings,
        "errors": report.errors,
        "duration_sec": round(report.duration_sec, 1),
        "test_summary": {
            "total": report.test_result.total,
            "passed": report.test_result.passed,
            "failed": report.test_result.failed,
            "pass_rate": round(report.test_result.pass_rate, 1),
        } if report.test_result else None,
    }

@router.post("/quality-gate/save-baseline")
async def save_quality_baseline():
    """Save current test results as quality baseline."""
    from backend.quality_gate import QualityGate, QualityGateConfig
    gate = QualityGate(QualityGateConfig.from_args())
    path = gate.save_baseline()
    return {"baseline_saved": path}


# ── Provider Proxy Routes ──────────────────────────────────────

@router.post("/proxy/translate-tools")
async def proxy_translate_tools(req: dict):
    """Translate tool definitions between Anthropic and OpenAI formats."""
    from backend.provider_proxy import translate_tools, ProviderFormat
    tools = req.get("tools", [])
    target = ProviderFormat(req.get("target", "openai_chat"))
    source = ProviderFormat(req.get("source", "anthropic"))
    result = translate_tools(tools, target, source)
    return {"translated_tools": result, "count": len(result)}

@router.post("/proxy/translate-messages")
async def proxy_translate_messages(req: dict):
    """Translate messages between Anthropic and OpenAI formats."""
    from backend.provider_proxy import anthropic_to_openai_chat, openai_chat_to_anthropic
    messages = req.get("messages", [])
    system = req.get("system", "")
    direction = req.get("direction", "anthropic_to_openai")

    if direction == "anthropic_to_openai":
        result = anthropic_to_openai_chat(messages, system)
    else:
        result, extracted_system = openai_chat_to_anthropic(messages, system)
        result = {"messages": result, "system": extracted_system}
    return {"result": result}


# ── Swarm Routes ───────────────────────────────────────────────

@router.get("/swarm/backends")
async def list_swarm_backends():
    """List available swarm backends."""
    from backend.swarm import get_backend_registry
    registry = get_backend_registry()
    available = registry.available_backends()
    return {"available_backends": [b.value for b in available]}

@router.post("/swarm/layouts")
async def get_swarm_layouts():
    """Get current swarm layout."""
    from backend.swarm.layout import TeammateLayout
    layout = TeammateLayout()
    return layout.to_dict()
