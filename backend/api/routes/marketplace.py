"""Aurora API - marketplace routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from backend.api.models import MarketplaceInstallRequest
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

class SkinCreateRequest(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    author: str = "Aurora"
    colors: dict = {}
    typography: dict = {}
    shape: dict = {}
    custom_css: str = ""
    accent_color_name: str = "purple"

class SkinImportRequest(BaseModel):
    data: dict

@router.get("/skins")

@router.get("/marketplace")
async def marketplace_list():
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    plugins = mp.list_available()
    return {"count": len(plugins), "plugins": plugins}

@router.post("/marketplace/install")
async def marketplace_install(req: MarketplaceInstallRequest):
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    result = mp.install_from_github(req.repo_url)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Install failed"))
    return result

@router.delete("/marketplace/{name}")
async def marketplace_uninstall(name: str):
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    result = mp.uninstall(name)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Uninstall failed"))
    return result

@router.get("/marketplace/{name}/updates")
async def marketplace_check_updates(name: str):
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    return mp.check_updates(name)

@router.get("/marketplace/search")
async def marketplace_search(q: str = ""):
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    results = mp.search(q)
    return {"query": q, "count": len(results), "results": results}



# === Skin / Theme Engine ===
async def skin_list():
    from backend.skin_engine import get_skin_manager
    return get_skin_manager().list_skins()

@router.get("/skins/active")
async def skin_active():
    from backend.skin_engine import get_skin_manager
    return get_skin_manager().get_active_skin()

@router.get("/skins/{name}")
async def skin_get(name: str):
    from backend.skin_engine import get_skin_manager
    skin = get_skin_manager().get_skin(name)
    if not skin:
        raise HTTPException(404, f"Skin '{name}' not found")
    return skin.to_dict()

@router.post("/skins")
async def skin_create(req: SkinCreateRequest):
    from backend.skin_engine import get_skin_manager
    data = req.model_dump()
    name = data.pop("name")
    skin = get_skin_manager().save_skin(name, data)
    return {"success": True, "skin": skin.to_dict()}

@router.delete("/skins/{name}")
async def skin_delete(name: str):
    from backend.skin_engine import get_skin_manager
    ok = get_skin_manager().delete_skin(name)
    if not ok:
        raise HTTPException(400, f"Cannot delete skin '{name}'")
    return {"success": True}

@router.post("/skins/{name}/apply")
async def skin_apply(name: str):
    from backend.skin_engine import get_skin_manager
    ok = get_skin_manager().apply_skin(name)
    if not ok:
        raise HTTPException(404, f"Skin '{name}' not found")
    return {"success": True, "active": name}

@router.get("/skins/{name}/export")
async def skin_export(name: str):
    from backend.skin_engine import get_skin_manager
    data = get_skin_manager().export_skin(name)
    if not data:
        raise HTTPException(404, f"Skin '{name}' not found")
    return data

@router.post("/skins/import")
async def skin_import(req: SkinImportRequest):
    from backend.skin_engine import get_skin_manager
    skin = get_skin_manager().import_skin(req.data)
    return {"success": True, "skin": skin.to_dict()}

# ═══════════ RE (Reverse Engineering) ═══════════

