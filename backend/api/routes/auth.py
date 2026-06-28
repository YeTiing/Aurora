"""Aurora API - auth routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Any, Optional

from backend.api.deps import cfg, llm, graph, rag, skills, plugins, ensure_all

router = APIRouter()

from backend.config import config as _cfg_module
from backend.agent.llm_client import LLMClient, LLMConfig

# Shared lazy deps
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

