"""Aurora API - detective routes"""
from __future__ import annotations
import logging
logger = logging.getLogger("aurora")
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

class ProviderProfile(BaseModel):
    name: str
    provider: str = "openai"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    max_context_tokens: int = 24000

@router.post("/detective/analyze")
async def detective_analyze(req: dict):
    from backend.diff_detective import get_detective
    d = get_detective(req.get("workspace", "."))
    line_str = req.get("lines", "")
    line_nums = [int(x) for x in line_str.split(",") if x.strip().isdigit()] if line_str else None
    result = await d.trace_bug_origin(req.get("file", ""), req.get("bug", "Bug investigation"), line_nums)
    return result

@router.get("/detective/blame")
async def detective_blame(file: str, lines: str = ""):
    from backend.diff_detective import get_detective
    d = get_detective()
    line_nums = None
    if lines:
        try: line_nums = [int(x) for x in lines.split(",") if x.strip().isdigit()]
        except Exception: logger.debug('detective provider profile failed', exc_info=True)
    report = await d.analyze_file(file, line_nums)
    return {
        "file": file,
        "suspicious_lines": [{"line": bl.line_no, "content": bl.content[:120], "commit": bl.commit_short, "author": bl.author, "date": bl.date} for bl in report.suspicious_lines[:30]],
        "suspect_commits": [{"hash": c.short_hash, "message": c.message[:150], "author": c.author} for c in report.suspect_commits[:8]],
        "hypothesis": report.root_cause_hypothesis,
    }


# ═══════════ Provider Profiles ═══════════


