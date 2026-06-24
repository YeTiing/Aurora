"""Aurora API - sessions routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Any, Optional

router = APIRouter()

from backend.api.models import IndexRequest

from backend.config import config as _cfg_module
from backend.agent.llm_client import LLMClient, LLMConfig


# Shared lazy deps
from backend.api.deps import (
    get_config as _get_cfg,
    get_llm as _get_llm,
    get_graph as _get_graph,
    get_rag as _get_rag,
    get_skills as _get_skills,
    get_plugins as _get_plugins,
)

# Alias for backward compatibility with existing route code
_cfg = None; _llm = None; _graph = None; _rag = None; _skills = None; _plugins = None

def _init_cfg():
    global _cfg
    _cfg = _get_cfg()

def _init_llm():
    global _llm
    _llm = _get_llm()

def _init_graph():
    global _graph
    _graph = _get_graph()

def _init_rag():
    global _rag
    _rag = _get_rag()

def _init_skills():
    global _skills
    _skills = _get_skills()

def _init():
    _init_cfg()

def _init_plugins():
    global _plugins
    _plugins = _get_plugins()





# ---- Inline Models ----

class TaskSubmitRequest(BaseModel):
    type: str = "rag_index"  # rag_index, llm_test
    path: str = ""
    message: str = "Hello, say connected in one short sentence."

@router.get("/sessions")
class GoalCreateRequest(BaseModel):
    objective: str
    token_budget: int | None = None

class GoalUpdateRequest(BaseModel):
    status: str  # "complete" or "blocked"

@router.post("/goal")
class SemanticIndexRequest(BaseModel):
    text: str
    metadata: dict | None = None

@router.get("/re/sessions")

@router.get("/agents/stats")
async def agent_stats():
    from backend.multi_agent import orchestrator; return orchestrator.stats()

# Context
@router.get("/context/stats")
async def context_stats():
    from backend.context import tracker; return tracker.summary()

# MCP
@router.get("/mcp/servers")
async def list_mcp_servers():
    from backend.mcp_hub import mcp_hub; return {"servers": mcp_hub.list_servers()}

@router.post("/checkpoint/undo")
async def undo_checkpoint():
    """Undo last checkpoint action."""
    from backend.agent.checkpoint import CheckpointManager
    mgr = CheckpointManager()
    cid = mgr.undo()
    if cid is None:
        return {"undone": False, "message": "Nothing to undo"}
    return {"undone": True, "checkpoint_id": cid}

@router.post("/checkpoint/redo")
async def redo_checkpoint():
    """Redo last undone checkpoint action."""
    from backend.agent.checkpoint import CheckpointManager
    mgr = CheckpointManager()
    cid = mgr.redo()
    if cid is None:
        return {"redone": False, "message": "Nothing to redo"}
    return {"redone": True, "checkpoint_id": cid}

@router.get("/checkpoint/list")
async def list_checkpoints():
    """List checkpoint history."""
    from backend.agent.checkpoint import CheckpointManager
    mgr = CheckpointManager()
    history = mgr.list_history()
    undo_count = sum(1 for h in history if h["type"] == "undo_stack")
    redo_count = sum(1 for h in history if h["type"] == "redo_stack")
    return {"history": history, "count": len(history), "undo_count": undo_count, "redo_count": redo_count}

# ── Task Queue ──
async def list_sessions():
    from backend.session_rollout import RolloutReader
    sessions = RolloutReader.list_sessions()
    return {"count": len(sessions), "sessions": sessions}

@router.get("/sessions/{session_id}/rollout")
async def get_session_rollout(session_id: str):
    from pathlib import Path
    from fastapi.responses import FileResponse
    from backend.session_rollout import _rollout_writers, RolloutReader
    sessions = RolloutReader.list_sessions()
    for s in sessions:
        if s.get("session_id") == session_id:
            fp = s.get("file", "")
            if fp and Path(fp).exists():
                return FileResponse(fp, media_type="application/x-ndjson",
                    filename=f"rollout-{session_id}.jsonl")
    raise HTTPException(404, f"No rollout found for session {session_id}")

@router.get("/sessions/{session_id}/stats")
async def get_session_stats(session_id: str):
    from backend.session_rollout import RolloutReader
    sessions = RolloutReader.list_sessions()
    for s in sessions:
        if s.get("session_id") == session_id:
            return s
    raise HTTPException(404, f"No stats found for session {session_id}")

# ══ Goal System ══

async def create_goal(req: GoalCreateRequest):
    from backend.goal import goal_manager
    goal = goal_manager.create_goal(req.objective, req.token_budget)
    return {"goal": goal.to_dict()}

@router.put("/goal/{goal_id}")
async def update_goal(goal_id: str, req: GoalUpdateRequest):
    from backend.goal import goal_manager
    if req.status not in ("complete", "blocked"):
        raise HTTPException(400, "Status must be 'complete' or 'blocked'")
    goal = goal_manager.update_goal(goal_id, req.status)
    if goal is None:
        raise HTTPException(404, f"Goal not found: {goal_id}")
    return {"goal": goal.to_dict()}

@router.get("/goal")
async def get_goal():
    from backend.goal import goal_manager
    return goal_manager.stats()

# ══ Context Budget ══

@router.get("/context/budget")
async def get_context_budget():
    from backend.context import TokenBudget as TokenAllocationBudget, tracker
    budget = TokenAllocationBudget()
    used = {"system": 1500, "tools": 2000, "rag": 0, "conversation": tracker.stats.total_prompt}
    return {
        "budget": {
            "total": budget.total,
            "system_prompt": budget.system_prompt,
            "tool_specs": budget.tool_specs,
            "conversation_history": budget.conversation_history,
            "output_reserve": budget.output_reserve,
        },
        "used": used,
        "available": budget.available(used),
        "tracker": tracker.summary(),
    }

# ========== Auth Routes ==========
@router.post("/auth/login")
async def auth_login(req: dict):
    from backend.auth import auth_manager
    api_key = req.get("api_key", req.get("key", ""))
    name = req.get("name", "default")
    if not api_key:
        raise HTTPException(400, "api_key is required")
    state = auth_manager.login_api_key(name, api_key)
    return auth_manager.get_active_auth()

@router.get("/memory/sessions/search")
async def memory_sessions_search(q: str = ""):
    """Search past sessions."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    results = dm.search_past_sessions(q)
    return {"query": q, "count": len(results), "results": results}

# === Semantic Memory ===
async def re_sessions():
    from backend.re_engine.session import get_re_manager
    return get_re_manager().list_sessions()

@router.get("/re/sessions/{session_id}")
async def re_session_get(session_id: str):
    from backend.re_engine.session import get_re_manager
    sess = get_re_manager().get(session_id)
    if not sess:
        raise HTTPException(404, f"RE session '{session_id}' not found")
    return {"id": sess.id, "url": sess.url, "stats": sess.stats(), "api_endpoints": sess.get_api_endpoints()[:50]}

@router.get("/re/sessions/{session_id}/requests")
async def re_session_requests(session_id: str, api_only: bool = False):
    from backend.re_engine.session import get_re_manager
    sess = get_re_manager().get(session_id)
    if not sess:
        raise HTTPException(404, f"RE session '{session_id}' not found")
    reqs = sess.get_requests(api_only=api_only)
    return {"session_id": session_id, "count": len(reqs), "requests": reqs[:100]}

@router.get("/re/sessions/{session_id}/hooks")
async def re_session_hooks(session_id: str):
    from backend.re_engine.session import get_re_manager
    sess = get_re_manager().get(session_id)
    if not sess:
        raise HTTPException(404, f"RE session '{session_id}' not found")
    return sess.get_hooks()

@router.get("/re/sessions/{session_id}/requests/{req_id}")
async def re_request_detail(session_id: str, req_id: str):
    from backend.re_engine.session import get_re_manager
    sess = get_re_manager().get(session_id)
    if not sess:
        raise HTTPException(404, f"RE session '{session_id}' not found")
    detail = sess.get_request_detail(req_id)
    if not detail:
        raise HTTPException(404, f"Request '{req_id}' not found")
    import json
    for key in ["request_headers", "response_headers"]:
        try: detail[key] = json.loads(detail[key]) if isinstance(detail[key], str) else detail[key]
        except: pass
    return detail

@router.get("/re/sessions/{session_id}/requests/{req_id}/curl")
async def re_request_curl(session_id: str, req_id: str):
    from backend.re_engine.session import get_re_manager
    import json, shlex
    sess = get_re_manager().get(session_id)
    if not sess: raise HTTPException(404, f"Session not found")
    detail = sess.get_request_detail(req_id)
    if not detail: raise HTTPException(404, f"Request not found")
    headers = json.loads(detail.get("request_headers","{}")) if isinstance(detail.get("request_headers"), str) else detail.get("request_headers",{})
    url = detail.get("url","")
    method = detail.get("method","GET")
    body = detail.get("request_body","")
    parts = ["curl", "-X", method]
    for k, v in headers.items():
        if k.lower() in ("host", "content-length", "connection", "accept-encoding"): continue
        parts.extend(["-H", f"{k}: {v}"])
    if body and method in ("POST","PUT","PATCH"): parts.extend(["-d", body[:2000]])
    parts.append(shlex.quote(url))
    return {"curl": " ".join(parts), "method": method, "url": url}

@router.get("/re/sessions/{session_id}/export/har")
async def re_export_har(session_id: str):
    from backend.re_engine.session import get_re_manager
    import json
    sess = get_re_manager().get(session_id)
    if not sess: raise HTTPException(404, f"Session not found")
    reqs = sess.get_requests()
    entries = []
    for r in reqs:
        req_headers = json.loads(r.get("request_headers","{}")) if isinstance(r.get("request_headers"),str) else r.get("request_headers",{})
        resp_headers = json.loads(r.get("response_headers","{}")) if isinstance(r.get("response_headers"),str) else r.get("response_headers",{})
        entries.append({
            "startedDateTime": "",
            "request": {"method": r.get("method","GET"), "url": r.get("url",""),
                "headers": [{"name": k, "value": v} for k,v in req_headers.items()],
                "postData": {"text": r.get("request_body","")[:50000]} if r.get("request_body") else {}},
            "response": {"status": r.get("response_status",0),
                "headers": [{"name": k, "value": v} for k,v in resp_headers.items()],
                "content": {"text": r.get("response_body","")[:50000]}},
        })
    return {"log": {"version": "1.2", "entries": entries}}

@router.post("/re/sessions/{session_id}/replay/{req_id}")
async def re_replay_request(session_id: str, req_id: str, modifier: dict = {}):
    from backend.re_engine.session import get_re_manager
    import httpx, json
    sess = get_re_manager().get(session_id)
    if not sess: raise HTTPException(404, f"Session not found")
    detail = sess.get_request_detail(req_id)
    if not detail: raise HTTPException(404, f"Request not found")
    headers = json.loads(detail.get("request_headers","{}")) if isinstance(detail.get("request_headers"),str) else detail.get("request_headers",{})
    url = detail.get("url","")
    method = detail.get("method","GET")
    body = detail.get("request_body","")
    if modifier:
        if "body" in modifier: body = modifier["body"]
        if "headers" in modifier: headers.update(modifier["headers"])
        if "url" in modifier: url = modifier["url"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, headers=headers, content=body)
            return {"status": resp.status_code, "headers": dict(resp.headers), "body": resp.text[:10000], "replayed": True}
    except Exception as e:
        return {"error": str(e), "replayed": False}

@router.delete("/re/sessions/{session_id}")
async def re_session_delete(session_id: str):
    from backend.re_engine.session import get_re_manager
    get_re_manager().delete(session_id)
    return {"success": True}

# ═══════════ Detective (Bug Forensics) ═══════════

@router.post("/sessions/export")
async def session_export_markdown(req: dict):
    from backend.session_export import export_session, export_session_json, ExportConfig
    fmt = req.get("format", "md")
    config = ExportConfig(
        include_tool_calls=req.get("include_tool_calls", True),
        include_plan=req.get("include_plan", True),
        include_timestamps=req.get("include_timestamps", True),
        include_system_messages=req.get("include_system_messages", False),
    )
    if fmt == "json":
        return {"format": "json", "content": export_session_json(req.get("session", req))}
    return {"format": "markdown", "content": export_session(req.get("session", req), config)}



@router.get("/search")
async def search_sessions(q: str = "", limit: int = 20):
    """Search across all sessions and their content."""
    try:
        from backend.session_search import session_search
        results = session_search.search(q, limit=limit)
        return {"query": q, "results": results, "count": len(results)}
    except ImportError:
        return {"error": "session_search not available"}

@router.get("/recent")
async def recent_sessions(limit: int = 10):
    """Get recently active sessions."""
    try:
        from backend.session_search import session_search
        results = session_search.recent(limit=limit)
        return {"sessions": results}
    except ImportError:
        return {"error": "session_search not available"}
