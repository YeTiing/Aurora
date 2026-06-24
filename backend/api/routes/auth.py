"""Aurora API - auth routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
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

def _init_plugins():
    global _plugins
    _plugins = _get_plugins()



@router.get("/auth/oauth/login")
async def auth_oauth_login(provider: str = "openai", redirect_uri: str = "http://localhost:8000/auth/oauth/callback"):
    from backend.auth import auth_manager
    result = auth_manager.start_oauth_flow(provider, redirect_uri)
    return result

@router.get("/auth/oauth/callback")
async def auth_oauth_callback(code: str, state: str):
    import httpx
    from backend.auth import auth_manager
    async with httpx.AsyncClient(timeout=30.0) as client:
        result = await auth_manager.complete_oauth_flow(code, state, client)
    return auth_manager.get_active_auth()

@router.get("/auth/status")
async def auth_status():
    from backend.auth import auth_manager
    return auth_manager.get_active_auth()

