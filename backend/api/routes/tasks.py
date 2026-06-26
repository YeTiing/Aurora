"""Aurora API - tasks routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from backend.api.models import TaskSubmitRequest
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Any, Optional

router = APIRouter()

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



@router.post("/tasks")
async def submit_task(req: TaskSubmitRequest):
    """Submit a background task."""
    from backend.task_queue import task_queue as tq
    from backend.api import _init_rag, _init_llm, _rag, _llm
    if req.type == "rag_index":
        _init_rag()
        import os as _os
        async def index_task(p):
            files = []
            for dirpath, dirnames, filenames in _os.walk(p):
                dirnames[:] = [d for d in dirnames if d not in (".git","node_modules","__pycache__","venv",".venv")]
                for fn in filenames:
                    files.append(_os.path.join(dirpath, fn))
            _rag.index_files(files)
            return f"Indexed {len(files)} files"
        task_id = await tq.submit(index_task, req.path, name="rag_index")
        return {"task_id": task_id, "type": "rag_index", "status": "pending"}
    elif req.type == "llm_test":
        _init_llm()
        async def llm_test_task(msg):
            resp = await _llm.chat([{"role":"user","content":msg}], max_tokens=50, temperature=0.1)
            return resp.content[:200]
        task_id = await tq.submit(llm_test_task, req.message, name="llm_test")
        return {"task_id": task_id, "type": "llm_test", "status": "pending"}
    else:
        raise HTTPException(400, f"Unknown task type: {req.type}")

@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get task status and result."""
    from backend.task_queue import task_queue as tq
    status = tq.status(task_id)
    if status is None:
        raise HTTPException(404, f"Task not found: {task_id}")
    return status

@router.get("/tasks")
async def list_tasks():
    """List all background tasks."""
    from backend.task_queue import task_queue as tq
    return {"tasks": tq.list_all(), "stats": tq.stats()}

@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running or pending task."""
    from backend.task_queue import task_queue as tq
    ok = tq.cancel(task_id)
    return {"task_id": task_id, "cancelled": ok}

# ── Prompt Presets ──
@router.get("/presets")
async def list_presets(search: str = ""):
    """List prompt presets, optionally search by keyword."""
    from backend.prompt_presets import preset_manager as pm
    if search:
        results = pm.search(search)
        return {"count": len(results), "presets": results}
    return {"count": len(pm.presets), "presets": pm.list_all(), "categories": pm.categories()}

@router.post("/cron")
async def cron_add(req: dict):
    """Add a cron task: {name, schedule, prompt}."""
    from backend.cron_scheduler import get_cron
    ok, msg = get_cron().add(
        req.get("name", ""),
        req.get("schedule", ""),
        req.get("prompt", ""),
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@router.delete("/cron/{name}")
async def cron_remove(name: str):
    """Remove a cron task."""
    from backend.cron_scheduler import get_cron
    ok = get_cron().remove(name)
    if not ok:
        raise HTTPException(404, f"Task not found: {name}")
    return {"removed": name}

@router.post("/cron/{name}/toggle")
async def cron_toggle(name: str):
    """Toggle a cron task on/off."""
    from backend.cron_scheduler import get_cron
    enabled = get_cron().toggle(name)
    return {"name": name, "enabled": enabled}

@router.get("/cron/stats")
async def cron_stats():
    """Get cron scheduler stats."""
    from backend.cron_scheduler import get_cron
    return get_cron().stats()


# ─── Automations (enhanced cron) ───

@router.get("/automations")
async def list_automations():
    """List all automations (cron tasks) with full details."""
    from backend.cron_scheduler import get_cron
    tasks = get_cron().list_tasks()
    return {"count": len(tasks), "automations": tasks, "stats": get_cron().stats()}

@router.post("/automations")
async def create_automation(req: dict):
    """Create an automation. Fields: name, schedule, prompt, rrule (optional), model (optional), reasoning_effort (optional)."""
    from backend.cron_scheduler import get_cron
    ok, msg = get_cron().add(
        name=req.get("name", ""),
        schedule_text=req.get("schedule", ""),
        prompt=req.get("prompt", ""),
        rrule=req.get("rrule"),
        model=req.get("model", ""),
        reasoning_effort=req.get("reasoning_effort", ""),
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@router.get("/automations/runs")
async def list_automation_runs(automation_id: str = "", limit: int = 50):
    """List automation runs, optionally filtered by automation_id."""
    from backend.sqlite_persistence import get_automation_runs_db
    db = get_automation_runs_db()
    if automation_id:
        runs = db.list_by_automation(automation_id, limit)
    else:
        runs = db.list_recent(limit)
    return {"count": len(runs), "runs": runs, "stats": db.stats()}

# ─── Inbox ───

@router.get("/inbox")
async def list_inbox(unread_only: bool = False, limit: int = 100):
    """List inbox items."""
    from backend.sqlite_persistence import get_inbox_items_db
    db = get_inbox_items_db()
    if unread_only:
        items = db.list_unread(limit)
    else:
        items = db.list_all(limit)
    return {"count": len(items), "items": items, "unread_count": db.unread_count()}

@router.post("/inbox/{item_id}/read")
async def mark_inbox_read(item_id: str):
    """Mark an inbox item as read."""
    from backend.sqlite_persistence import get_inbox_items_db
    db = get_inbox_items_db()
    db.mark_read(item_id)
    return {"id": item_id, "read": True}


# === Observability Stats ===
@router.get("/observability/stats")
async def observability_stats():
    from backend.observability.stats import get_stats
    return get_stats()


# === Dual-File Memory (Hermes-style) ===
@router.get("/memory/agent")
async def memory_agent_list():
    """List agent memory entries."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.agent_memory.list_entries()

