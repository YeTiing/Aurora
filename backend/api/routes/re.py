"""Aurora API - re routes"""
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



@router.post("/re/capture/start")
async def re_capture_start(req: dict = {}):
    from backend.re_engine.capture import get_capture_engine
    eng = get_capture_engine()
    sess = eng.start_session(req.get("url", ""))
    hooks = eng.get_hook_scripts()
    return {"session_id": sess.id, "hooks": hooks}

@router.post("/re/capture/stop")
async def re_capture_stop():
    from backend.re_engine.capture import get_capture_engine
    return get_capture_engine().stop_session()

@router.post("/re/capture/request")
async def re_capture_request(req: dict):
    from backend.re_engine.capture import get_capture_engine
    r = get_capture_engine().capture_mitm_request(req)
    return {"id": r.id, "seq": r.seq, "method": r.method, "url": r.url[:200]}

@router.post("/re/deobfuscate")
async def re_deobfuscate(req: dict):
    from backend.re_engine.deobfuscator import get_deobfuscator
    d = get_deobfuscator()
    code = req.get("code", "")
    filepath = req.get("file", "")
    if filepath:
        from pathlib import Path
        p = Path(filepath)
        if p.exists():
            code = p.read_text(encoding="utf-8", errors="ignore")
        else:
            raise HTTPException(400, f"File not found: {filepath}")
    if not code:
        raise HTTPException(400, "Provide 'code' or 'file'")
    result = d.analyze(code, filepath or "inline.js")
    return result

@router.post("/re/mine")
async def re_mine(req: dict):
    from backend.re_engine.miner import get_api_miner
    m = get_api_miner()
    session_id = req.get("session_id", "")
    filepath = req.get("file", "")
    code = req.get("code", "")
    results = []
    if session_id:
        results = m.mine_from_session(session_id)
    elif filepath:
        results = m.mine_file(filepath)
    elif code:
        results = m.mine_text(code, "inline")
    else:
        raise HTTPException(400, "Provide 'session_id', 'file', or 'code'")
    return {"count": len(results), "endpoints": results[:100]}

@router.post("/re/analyze")
async def re_analyze(req: dict):
    from backend.re_engine.analyzer import get_analyzer
    a = get_analyzer()
    session_id = req.get("session_id", "")
    if session_id:
        return a.analyze_session(session_id)
    code = req.get("code", "")
    url = req.get("url", "")
    return {"scene": a.detect_scene(url, "", code), "auth": a.trace_auth("", code), "crypto": a.fingerprint_crypto(code)}



@router.post("/re/import/har")
async def re_import_har(req: dict):
    from backend.re_engine.session import get_re_manager, CapturedRequest
    import json
    har_data = req.get("har", req)
    if isinstance(har_data, str):
        try: har_data = json.loads(har_data)
        except Exception: raise HTTPException(400, "Invalid HAR JSON")
    mgr = get_re_manager()
    sess = mgr.create(url=req.get("url","HAR Import"))
    entries = har_data.get("log",{}).get("entries",[]) or []
    for entry in entries:
        rq = entry.get("request",{})
        rs = entry.get("response",{})
        req_obj = CapturedRequest(
            id=f"har_{sess.id}_{len(entries)}", session_id=sess.id,
            seq=len(entries)+1, method=rq.get("method","GET"),
            url=rq.get("url",""), path=rq.get("url","").split("?",1)[0] if "?" in rq.get("url","") else rq.get("url",""),
            request_headers=json.dumps({h.get("name",""):h.get("value","") for h in rq.get("headers",[])}),
            request_body=rq.get("postData",{}).get("text","")[:50000] if isinstance(rq.get("postData"), dict) else "",
            response_status=rs.get("status",0),
            response_headers=json.dumps({h.get("name",""):h.get("value","") for h in rs.get("headers",[])}),
            response_body=rs.get("content",{}).get("text","")[:50000] if isinstance(rs.get("content"), dict) else "",
        )
        sess.add_request(req_obj)
    return {"session_id": sess.id, "url": sess.url, "imported": len(entries), "stats": sess.stats()}

@router.post("/re/signature")
async def re_signature_trace(req: dict):
    from backend.re_engine.signature import get_signature_tracer
    t = get_signature_tracer()
    code = req.get("code", "")
    filepath = req.get("file", "")
    if filepath:
        from pathlib import Path
        p = Path(filepath)
        if p.exists(): code = p.read_text(encoding="utf-8", errors="ignore")
        else: raise HTTPException(400, f"File not found: {filepath}")
    if not code: raise HTTPException(400, "Provide 'code' or 'file'")
    return t.trace(code)

