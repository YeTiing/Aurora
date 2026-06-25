"""Aurora API - memory routes"""
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

class MarketplaceInstallRequest(BaseModel):
    repo_url: str

@router.get("/memory/agent/stats")
async def memory_agent_stats():
    """Get agent memory stats."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.agent_memory.stats()

@router.post("/memory/agent")
async def memory_agent_add(req: dict):
    """Add an entry to agent memory."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.agent_memory.add(req.get("text", ""), source="api")
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@router.put("/memory/agent/{index}")
async def memory_agent_replace(index: int, req: dict):
    """Replace an entry in agent memory."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.agent_memory.replace(index, req.get("text", ""), source="api")
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@router.delete("/memory/agent/{index}")
async def memory_agent_remove(index: int):
    """Remove an entry from agent memory."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.agent_memory.remove(index)
    if not ok:
        raise HTTPException(404, msg)
    return {"success": True, "message": msg}

@router.get("/memory/user")
async def memory_user_list():
    """List user profile entries."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.user_profile.list_entries()

@router.get("/memory/user/stats")
async def memory_user_stats():
    """Get user profile stats."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.user_profile.stats()

@router.post("/memory/user")
async def memory_user_add(req: dict):
    """Add an entry to user profile."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.user_profile.add(req.get("text", ""), source="api")
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@router.put("/memory/user/{index}")
async def memory_user_replace(index: int, req: dict):
    """Replace an entry in user profile."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.user_profile.replace(index, req.get("text", ""), source="api")
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@router.delete("/memory/user/{index}")
async def memory_user_remove(index: int):
    """Remove an entry from user profile."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.user_profile.remove(index)
    if not ok:
        raise HTTPException(404, msg)
    return {"success": True, "message": msg}

@router.get("/memory/stats")
async def memory_stats():
    """Get full memory system stats."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.stats()

@router.post("/memory/curator/run")
async def memory_curator_run():
    """Run lightweight curator deduplication."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    result = dm.curator.run_lightweight()
    return {"success": True, "result": result}

@router.post("/memory/session/{session_id}/end")
async def memory_session_end(session_id: str, req: dict):
    """End session: run curator, index summary."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    result = dm.end_session(session_id, req.get("summary", ""))
    return {"success": True, "result": result}

@router.post("/memory/semantic/index")
async def semantic_index(req: SemanticIndexRequest):
    from backend.memory.semantic import get_semantic_memory
    sm = get_semantic_memory()
    mid = sm.index(req.text, req.metadata)
    return {"id": mid, "indexed": True}

@router.get("/memory/semantic/search")
async def semantic_search(q: str = "", k: int = 5):
    from backend.memory.semantic import get_semantic_memory
    sm = get_semantic_memory()
    results = sm.search(q, top_k=k)
    return {"query": q, "count": len(results), "results": results}

@router.delete("/memory/semantic/{memory_id}")
async def semantic_forget(memory_id: str):
    from backend.memory.semantic import get_semantic_memory
    sm = get_semantic_memory()
    ok = sm.forget(memory_id)
    if not ok:
        raise HTTPException(404, f"Memory not found: {memory_id}")
    return {"id": memory_id, "forgotten": True}

@router.post("/memory/semantic/build-episodic")
async def semantic_build_episodic():
    from backend.memory.semantic import get_semantic_memory
    sm = get_semantic_memory()
    count = sm.build_episodic_index()
    return {"indexed_episodes": count}


# === Skills ===
@router.get("/memory/skills")
async def memory_skills_list():
    _init()
    from backend.dual_memory import get_closed_loop
    return get_closed_loop().skills.all()

@router.post("/memory/skills/{name}/use")
async def memory_skill_use(name: str):
    _init()
    from backend.dual_memory import get_closed_loop
    get_closed_loop().skills.use(name)
    return {"used": name}

@router.delete("/memory/skills/{name}")
async def memory_skill_archive(name: str):
    _init()
    from backend.dual_memory import get_closed_loop
    ok = get_closed_loop().skills.archive(name)
    if not ok: raise HTTPException(404, "Skill not found")
    return {"archived": name}

# === Honcho ===
@router.get("/memory/honcho")
async def memory_honcho_status():
    _init()
    from backend.dual_memory import get_closed_loop
    cl = get_closed_loop()
    return {
        "turns": cl.honcho._turns,
        "traits": cl.honcho.peer.traits,
        "preferences": cl.honcho.peer.preferences,
        "knowledge": cl.honcho.peer.knowledge_levels,
        "contradictions": cl.honcho.peer.contradictions,
        "context": cl.honcho.prompt_injection(),
    }

# === Nudge ===
@router.post("/memory/nudge/trigger")
async def memory_nudge_trigger():
    _init()
    from backend.dual_memory import get_closed_loop
    cl = get_closed_loop()
    p = cl.nudge.end_prompt(len(cl.agent_memory.entries))
    return {"nudge": p}

# === Curator ===
@router.post("/memory/curator/full")
async def memory_curator_full():
    _init()
    from backend.dual_memory import get_closed_loop
    cl = get_closed_loop()
    # Use mock if no real LLM configured
    try:
        from backend.config import config
    except Exception:
        config = None
    result = await cl.curator.light()  # Fallback to lightweight
    return {"curation": result}

# === FTS5 Sessions ===
@router.get("/memory/sessions/recent")
async def memory_sessions_recent(n: int = 10):
    _init()
    from backend.dual_memory import get_closed_loop
    return get_closed_loop().fts5.recent(n)

# === Plugin Marketplace ===

