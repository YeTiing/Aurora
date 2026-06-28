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

from backend.api.deps import cfg, llm, graph, rag, skills, plugins, ensure_all

router = APIRouter()

from backend.config import config as _cfg_module
from backend.agent.llm_client import LLMClient, LLMConfig

# Shared lazy deps
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

