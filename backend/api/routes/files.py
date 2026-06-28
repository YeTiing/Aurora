"""Aurora API - files routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Any, Optional

from backend.api.deps import cfg, llm, graph, rag, skills, plugins, ensure_all

router = APIRouter()

from backend.api.models import IndexRequest

from backend.config import config as _cfg_module
from backend.api.path_security import resolve_allowed_path

# Shared lazy deps
def _resolve_workspace_path(path: str, workspace: str = ".") -> Path:
    ensure_all()
    return resolve_allowed_path(path, workspace, cfg())

def _mask_config_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("api_key", "apikey", "access_key", "privatekey", "private_key", "token", "secret", "password", "credential", "authorization")):
                masked[key] = "***" if item else item
            else:
                masked[key] = _mask_config_secrets(item)
        return masked
    if isinstance(value, list):
        return [_mask_config_secrets(item) for item in value]
    return value

@router.get("/files/read")
async def read_file(path: str, workspace: str = "."):
    p = _resolve_workspace_path(path, workspace)
    if not p.exists() or not p.is_file(): raise HTTPException(404, "Not found")
    return {"path":str(p),"content":p.read_text(encoding="utf-8", errors="ignore")}

@router.post("/files/write")
async def write_file(req: dict):
    p = _resolve_workspace_path(req["path"], str(req.get("workspace", "."))); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.get("content",""), encoding="utf-8")
    return {"path":str(p),"written":True}

@router.post("/files/search")
async def search_files(req: dict):
    import subprocess
    try:
        search_root = _resolve_workspace_path(str(req.get("path",".")), str(req.get("workspace", ".")))
        r = subprocess.run(["rg","--line-number","--max-count",str(req.get("max_results",50)),req.get("query",""),str(search_root)], capture_output=True, text=True, timeout=10)
        return {"results":r.stdout[:10000],"count":len(r.stdout.splitlines())}
    except HTTPException:
        raise
    except Exception as e:
        import logging; logging.getLogger("aurora").warning(f"search_files failed: {e}")
        return {"results":"","count":0,"error":f"Search failed: {type(e).__name__}"}

# RAG
@router.post("/rag/index")
async def index_project(req: IndexRequest):
    ensure_all(); import os
    index_root = _resolve_workspace_path(req.path, req.workspace)
    files = []
    for dirpath, dirnames, filenames in os.walk(index_root):
        dirnames[:] = [d for d in dirnames if d not in (".git","node_modules","__pycache__","venv",".venv")]
        for f in filenames: files.append(os.path.join(dirpath, f))
    rag().index_files(files)
    return {"indexed":len(files)}

@router.get("/rag/search")
async def search_rag(query: str, top_k: int = 5):
    ensure_all()
    chunks = rag().search(query, top_k=top_k, llm_client=_llm)
    return {"count":len(chunks),"results":[{"content":c["content"][:200],"file":c["metadata"].get("file","")} for c in chunks]}

# Sessions
@router.get("/sessions")
async def list_active_sessions():
    ws_count = 0
    try:
        from backend.api.routes.chat import _ws_connections
        ws_count = sum(len(v) for v in _ws_connections.values())
    except ImportError:
        pass
    from backend.session_registry import list_active
    active = list_active()
    return {"sessions": active, "ws_connections": ws_count}

# Tools
@router.get("/tools")
async def list_tools():
    from backend.tools import tool_registry
    return {"tools":[{"name":s.name,"description":s.description,"category":s.category} for s in tool_registry.list_tools()],"stats":tool_registry.stats()}

# Config
@router.get("/config")
async def get_config(): ensure_all(); return _mask_config_secrets(cfg().all())

