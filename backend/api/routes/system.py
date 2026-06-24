"""Aurora API - system routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
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

def _init_plugins():
    global _plugins
    _plugins = _get_plugins()





# ---- Inline Models ----

class ApprovalAction(BaseModel):
    request_id: str
    action: str  # approve / deny

class CreateThreadRequest(BaseModel):
    title: str = ""; workspace: str = ""; model: str = ""; reasoning_effort: str = "medium"; thread_source: str = "user"

class DiffMergeRequest(BaseModel):
    base: str; ours: str; theirs: str

@router.post("/plugins/{name}/load")
async def load_plugin(name: str): _init_plugins(); return {"name":name,"loaded":_plugins.load(name)}

@router.post("/plugins/{name}/unload")
async def unload_plugin(name: str): _init_plugins(); return {"name":name,"unloaded":_plugins.unload(name)}

@router.post("/mcp/servers/start")
async def start_mcp_server(req: dict):
    from backend.mcp_hub import mcp_hub, MCPServerConfig
    cfg = MCPServerConfig(**{k: req.get(k,v) for k,v in MCPServerConfig.__dataclass_fields__.items() if k in req or hasattr(MCPServerConfig,k)})
    return {"name":cfg.name,"started":await mcp_hub.start_server(cfg)}

@router.post("/mcp/servers/{name}/stop")
async def stop_mcp_server(name: str):
    from backend.mcp_hub import mcp_hub; await mcp_hub.stop_server(name); return {"name":name,"stopped":True}

# Processes
@router.get("/processes")
async def list_processes():
    from backend.process_manager import process_manager
    procs = process_manager.list_all()
    return {"processes":[{"id":p.id,"command":p.command,"status":p.status} for p in procs],"stats":process_manager.stats()}

@router.post("/processes/{proc_id}/kill")
async def kill_process(proc_id: str):
    from backend.process_manager import process_manager
    return {"id":proc_id,"killed":process_manager.kill(proc_id)}

# Storage
@router.get("/storage/stats")
async def storage_stats():
    from backend.sqlite_persistence import get_thread_goals_db, get_logs_db, get_memories_db
    return {"goals":get_thread_goals_db().stats(),"memories":len(get_memories_db().list_keys())}

@router.get("/storage/memories")
async def list_memories(category: str|None=None):
    from backend.sqlite_persistence import get_memories_db
    db = get_memories_db(); keys = db.list_keys(category)
    return {"count":len(keys),"memories":{k:db.get(k) for k in keys[:50]}}

# ═══════════ Settings / Models / LLM Test ═══════════

@router.post("/browser/navigate")
async def browser_navigate(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.navigate(req.get("url", "https://google.com"))
    return {"ok": result.get("error") is None, "result": result}

@router.post("/browser/screenshot")
async def browser_screenshot():
    from backend.browser_use import browser_use
    result = await browser_use.screenshot()
    return {"ok": result.get("error") is None, **result}

@router.post("/browser/click")
async def browser_click(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.click(req.get("selector", "body"))
    return {"ok": result.get("error") is None, "result": result}

@router.post("/browser/type")
async def browser_type(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.type_text(req.get("selector", "input"), req.get("text", ""))
    return {"ok": True, "result": result}

# ═══════════ Approval ═══════════
@router.get("/approval/status")
async def approval_status():
    from backend.approval import approval_manager
    return approval_manager.stats()

@router.get("/approval/pending")
async def approval_pending():
    from backend.approval import approval_manager
    pending = approval_manager.get_pending()
    return {"count": len(pending), "requests": [{"id": r.id, "type": r.type, "risk": r.risk_level.value, "description": r.description} for r in pending]}

@router.post("/approval/{action}")
async def approval_act(action: str, req: ApprovalAction):
    from backend.approval import approval_manager
    if action == "approve":
        ok = approval_manager.approve(req.request_id)
    else:
        ok = approval_manager.deny(req.request_id)
    return {"request_id": req.request_id, "action": action, "ok": ok}

# ═══════════ Threads ═══════════
@router.get("/threads")
async def list_threads(status: str = ""):
    from backend.thread_manager import thread_manager
    threads = thread_manager.list_threads(status)
    return {"count": len(threads), "threads": [{"id": t.id, "title": t.title, "status": t.status, "model": t.model, "reasoning_effort": t.reasoning_effort} for t in threads]}

@router.post("/threads/{thread_id}/fork")
async def fork_thread(thread_id: str, req: dict = {}):
    from backend.thread_manager import thread_manager
    t = thread_manager.fork(thread_id, req.get("title", ""))
    if not t: raise HTTPException(404, "Thread not found")
    return {"id": t.id, "title": t.title, "forked_from": thread_id}

@router.get("/threads/{thread_id}")
async def read_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    t = thread_manager.read(thread_id)
    if not t: raise HTTPException(404, "Not found")
    return {"id": t.id, "title": t.title, "status": t.status, "workspace": t.workspace, "model": t.model, "reasoning_effort": t.reasoning_effort, "parent_id": t.parent_id}

@router.post("/threads/{thread_id}/pin")
async def pin_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_pinned(thread_id)
    return {"id": thread_id, "pinned": ok}

@router.post("/threads/{thread_id}/archive")
async def archive_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_archived(thread_id)
    return {"id": thread_id, "archived": ok}

@router.post("/threads/{thread_id}/title")
async def rename_thread(thread_id: str, req: dict):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_title(thread_id, req.get("title", ""))
    return {"id": thread_id, "renamed": ok}

# ═══════════ Heartbeat ═══════════
@router.get("/heartbeat/status")
async def heartbeat_status():
    from backend.heartbeat import heartbeat_manager
    return {"enabled": heartbeat_manager._enabled, "interval": heartbeat_manager._config.interval_seconds}

@router.post("/heartbeat/send")
async def heartbeat_send(req: dict):
    from backend.heartbeat import heartbeat_manager
    msg = heartbeat_manager.notify(req.get("message", "Checking in"))
    return {"heartbeat": msg[:200]}

# ═══════════ Sentry ═══════════
@router.post("/sentry/test")
async def test_sentry():
    try:
        import sentry_sdk
        sentry_sdk.capture_message("Aurora Sentry test message", level="info")
        return {"ok": True, "message": "Test message sent"}
    except ImportError:
        return {"ok": False, "message": "sentry-sdk not installed"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}

# ═══════════ AGENTS.md ═══════════
@router.get("/agents-md")
async def list_agents_md():
    from backend.agents_md import agents_loader
    rules = agents_loader.scan(".")
    return {"count": len(rules), "files": [str(r.file_path) for r in rules]}

@router.get("/agents-md/read")
async def read_agents_md(path: str = "."):
    from backend.agents_md import agents_loader
    content = agents_loader.inject(path)
    return {"path": path, "has_instructions": bool(content), "content": content[:3000]}


# ── File Upload ──
@router.post("/files/upload")
async def upload_file(file: UploadFile = File(...), workspace: str = Form(".")):
    """Upload a file to the workspace uploads directory."""
    if not file.filename:
        raise HTTPException(400, "No file provided")
    ws_dir = Path(workspace) / "uploads"
    ws_dir.mkdir(parents=True, exist_ok=True)
    file_path = ws_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)
    mime_type, _ = mimetypes.guess_type(file.filename)
    mime_type = mime_type or "application/octet-stream"
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
    ext = file_path.suffix.lower()
    is_image = ext in image_exts
    result = {
        "filename": file.filename,
        "path": str(file_path),
        "size": file_path.stat().st_size,
        "mime_type": mime_type,
        "is_image": is_image,
    }
    if is_image:
        b64 = base64.b64encode(content).decode("ascii")
        result["data_url"] = f"data:{mime_type};base64,{b64}"
    return result

# ── Diff Engine ──
async def auth_logout():
    from backend.auth import auth_manager
    return auth_manager.logout()

# ========== Tool Call Blocks Debug ==========
@router.get("/tools/blocks")
async def tools_blocks():
    from backend.agent.tool_call_aggregator import tool_call_aggregator
    return tool_call_aggregator.get_snapshot()

@router.post("/tools/blocks/reset")
async def tools_blocks_reset():
    from backend.agent.tool_call_aggregator import tool_call_aggregator
    tool_call_aggregator.reset()
    return {"reset": True, "snapshot": tool_call_aggregator.get_snapshot()}

# ========== Model Discovery ==========
@router.post("/models/discover")
async def models_discover(req: dict):
    from backend.model_discovery import model_discovery
    provider = req.get("provider", "openai")
    api_key = req.get("api_key", "")
    base_url = req.get("base_url", "")
    if provider == "openai":
        models = await model_discovery.discover_openai_models(api_key, base_url or "https://api.openai.com/v1")
    elif provider == "ollama":
        models = await model_discovery.discover_ollama_models(base_url or "http://localhost:11434")
    elif provider == "groq":
        models = await model_discovery.discover_groq_models(api_key)
    elif provider == "deepseek":
        models = await model_discovery.discover_deepseek_models(api_key)
    else:
        raise HTTPException(400, f"Unknown provider: {provider}")
    result = []
    for m in models:
        result.append({
            "id": m.id, "provider": m.provider, "owned_by": m.owned_by,
            "context_window": m.context_window, "reasoning_support": m.reasoning_support,
            "vision_support": m.vision_support, "function_calling": m.function_calling,
            "streaming": m.streaming, "input_price": m.input_price,
            "output_price": m.output_price, "speed_tier": m.speed_tier,
            "recommended_for": m.recommended_for, "available": m.available,
        })
    return {"provider": provider, "count": len(result), "models": result}

