"""Aurora API - files routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
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



@router.get("/files/read")
async def read_file(path: str):
    p = Path(path)
    if not p.exists(): raise HTTPException(404, "Not found")
    return {"path":str(p),"content":p.read_text(encoding="utf-8", errors="ignore")}

@router.post("/files/write")
async def write_file(req: dict):
    p = Path(req["path"]); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.get("content",""), encoding="utf-8")
    return {"path":str(p),"written":True}

@router.post("/files/search")
async def search_files(req: dict):
    import subprocess
    try:
        r = subprocess.run(["rg","--line-number","--max-count",str(req.get("max_results",50)),req.get("query",""),str(req.get("path","."))], capture_output=True, text=True, timeout=10)
        return {"results":r.stdout[:10000],"count":len(r.stdout.splitlines())}
    except Exception as e:
        import logging; logging.getLogger("aurora").warning(f"search_files failed: {e}")
        return {"results":"","count":0,"error":f"Search failed: {type(e).__name__}"}

# RAG
@router.post("/rag/index")
async def index_project(req: IndexRequest):
    _init_rag(); import os
    files = []
    for dirpath, dirnames, filenames in os.walk(req.path):
        dirnames[:] = [d for d in dirnames if d not in (".git","node_modules","__pycache__","venv",".venv")]
        for f in filenames: files.append(os.path.join(dirpath, f))
    _rag.index_files(files)
    return {"indexed":len(files)}

@router.get("/rag/search")
async def search_rag(query: str, top_k: int = 5):
    _init_rag()
    chunks = _rag.search(query, top_k=top_k, llm_client=_llm)
    return {"count":len(chunks),"results":[{"content":c["content"][:200],"file":c["metadata"].get("file","")} for c in chunks]}

# Sessions
_sessions_meta: dict[str, dict] = {}
@router.get("/sessions")
async def list_sessions():
    return {"sessions":list(_sessions_meta.values()),"ws_connections":sum(len(v) for v in _ws_connections.values())}

# Tools
@router.get("/tools")
async def list_tools():
    from backend.tools import tool_registry
    return {"tools":[{"name":s.name,"description":s.description,"category":s.category} for s in tool_registry.list_tools()],"stats":tool_registry.stats()}

# Config
@router.get("/config")
async def get_config(): _init(); return _cfg.all()

