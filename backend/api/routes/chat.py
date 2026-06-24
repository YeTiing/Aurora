"""Aurora API - chat routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Any, Optional

from backend.thread_follower import ThreadFollower, ThreadSettings

router = APIRouter()
thread_follower = ThreadFollower()

from backend.api.models import ChatRequest, AgentResponse

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



@router.post("/soul")
async def soul_update(req: dict):
    """Update SOUL.md personality."""
    from pathlib import Path
    sp = Path(".aurora") / "SOUL.md"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(req.get("content", ""), encoding="utf-8")
    return {"updated": True, "path": str(sp)}

# Health
@router.get("/health")
async def health():
    return {"status":"ok","version":"0.2.0","timestamp":time.time()}

# Chat
@router.post("/chat")
async def chat(req: ChatRequest):
    sid = req.session_id or f"session_{uuid.uuid4().hex[:8]}"
    _init_graph(); _init_skills()
    skills_ctx = ""; rag_ctx = ""
    if _skills:
        triggered = _skills.match(req.message)
        skills_ctx = _skills.inject(triggered)
    _init_rag()
    if _rag and _rag.vector_store.count() > 0:
        chunks = _rag.search(req.message, top_k=5, llm_client=_llm)
        if chunks: rag_ctx = _rag.format_context(chunks)
    full = f"{skills_ctx}\
{rag_ctx}\
User: {req.message}" if (skills_ctx or rag_ctx) else req.message
    history = [{"role": h.get("role","user"), "content": h.get("content","")} for h in (req.history or [])]
    state = await _graph.run(full, session_id=sid, workspace=req.workspace, history=history)
    return AgentResponse(session_id=sid, response=state.final_response, plan=[p.to_dict() for p in state.plan], diffs=state.diffs)

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    sid = req.session_id or f"session_{uuid.uuid4().hex[:8]}"
    _init_graph(); _init_skills(); _init_rag()
    full = req.message
    history2 = [{"role": h.get("role","user"), "content": h.get("content","")} for h in (req.history or [])]
    async def gen():
        async for chunk in _graph.run_with_stream(full, session_id=sid, workspace=req.workspace, history=history2):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\
\
"
    return StreamingResponse(gen(), media_type="text/event-stream")

# WebSocket — Desktop connection (for BrowserView CDP relay)
_desktop_ws: WebSocket | None = None

@router.websocket("/ws/desktop")
async def desktop_websocket(ws: WebSocket):
    """Desktop WebSocket - handles chat + browser relay."""
    global _desktop_ws
    await ws.accept()
    from backend.browser_relay import browser_relay
    browser_relay.set_ws(ws)
    _desktop_ws = ws
    
    # Subscribe to SSE events for this connection
    from backend.agent.sse_events import sse_bus
    async def forward_event(event):
        try:
            await ws.send_text(json.dumps(event.to_dict(), ensure_ascii=False))
        except Exception:
            pass
    sse_bus.subscribe("desktop", forward_event)
    thread_follower.set_event_emit(sse_bus.emit)

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            
            # Browser command results from desktop
            if msg.get("type") == "browser_result":
                browser_relay.on_result(msg.get("id", ""), msg.get("result", {}))
                continue
            
            # Chat message from desktop - handle it
            if msg.get("type") == "chat":
                from backend.api.deps import get_graph as _get_graph
                session_id = msg.get("sessionId") or f"session_{uuid.uuid4().hex[:8]}"
                sandbox_mode = msg.get("sandboxMode", "full-access")
                model = msg.get("model", "")
                user_text = msg.get("message", "")
                workspace = msg.get("workspace", ".")
                
                await ws.send_text(json.dumps({
                    "type": "codex/event/user_message",
                    "data": {"content": user_text},
                    "session_id": session_id,
                }, ensure_ascii=False))
                
                try:
                    _init_graph(); graph = _graph
                    history = [{"role": h.get("role","user"), "content": h.get("content","")} for h in (msg.get("history") or [])]
                    await thread_follower.start_turn(
                        thread_id=session_id,
                        session_id=session_id,
                        message=user_text,
                        settings=ThreadSettings(
                            model=model,
                            sandbox_policy=sandbox_mode,
                            approval_mode=msg.get("approvalMode", "on-request"),
                            reasoning_effort=msg.get("reasoningEffort", "medium"),
                        ),
                    )
                    state = await graph.run(
                        user_text,
                        session_id=session_id,
                        workspace=workspace,
                        sandbox_mode=sandbox_mode,
                        model=model,
                        history=history,
                    )
                    if state.final_response:
                        await ws.send_text(json.dumps({
                            "type": "codex/event/agent_message",
                            "data": {"content": state.final_response},
                            "session_id": session_id,
                        }, ensure_ascii=False))
                    await ws.send_text(json.dumps({
                        "type": "done",
                        "response": state.final_response,
                        "session_id": session_id,
                        "tokens": state.total_turns,
                    }, ensure_ascii=False))
                except Exception as e:
                    import traceback
                    await ws.send_text(json.dumps({
                        "type": "codex/event/error",
                        "data": {"error": str(e), "traceback": traceback.format_exc()[:500]},
                        "session_id": session_id,
                    }, ensure_ascii=False))
                continue
            
            # ThreadFollower controls from desktop
            if msg.get("type") == "thread_control":
                session_id = msg.get("sessionId") or msg.get("threadId") or "desktop"
                thread_id = msg.get("threadId") or session_id
                action = msg.get("action", "")
                try:
                    if action == "steer":
                        result = await thread_follower.steer_turn(thread_id, msg.get("instruction", ""))
                    elif action == "interrupt":
                        result = await thread_follower.interrupt_turn(thread_id, msg.get("reason", "user_requested"))
                    elif action == "compact":
                        result = await thread_follower.compact_thread(thread_id, float(msg.get("tokenUsageRatio", 0.9)))
                    elif action == "settings":
                        current = thread_follower.get_thread(thread_id).settings
                        result = await thread_follower.update_thread_settings(
                            thread_id,
                            ThreadSettings(
                                model=msg.get("model", current.model),
                                reasoning_effort=msg.get("reasoningEffort", current.reasoning_effort),
                                sandbox_policy=msg.get("sandboxMode", current.sandbox_policy),
                                approval_mode=msg.get("approvalMode", current.approval_mode),
                            ),
                        )
                    elif action == "followups":
                        result = await thread_follower.set_queued_followups(thread_id, msg.get("followups", []))
                    else:
                        result = {"error": f"Unknown thread control action: {action}"}
                    await ws.send_text(json.dumps({
                        "type": "thread_control_result",
                        "action": action,
                        "data": result,
                        "session_id": session_id,
                        "thread_id": thread_id,
                    }, ensure_ascii=False))
                except KeyError:
                    await ws.send_text(json.dumps({
                        "type": "codex/event/error",
                        "data": {"error": f"Unknown thread: {thread_id}"},
                        "session_id": session_id,
                        "thread_id": thread_id,
                    }, ensure_ascii=False))
                continue

            # Cancel message
            if msg.get("type") == "cancel":
                session_id = msg.get("sessionId") or "desktop"
                try:
                    await thread_follower.interrupt_turn(session_id, "user_cancelled")
                except KeyError:
                    pass
                await ws.send_text(json.dumps({
                    "type": "codex/event/turn_aborted",
                    "data": {"reason": "user_cancelled"},
                    "session_id": session_id,
                    "thread_id": session_id,
                }, ensure_ascii=False))

    except WebSocketDisconnect:
        pass
    finally:
        sse_bus.unsubscribe("desktop", forward_event)
        browser_relay.clear_ws()
        _desktop_ws = None

# WebSocket — Session connections (kept for backward compat)
_ws_connections: dict[str, list[WebSocket]] = {}

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    _ws_connections.setdefault(session_id, []).append(ws)

    # Subscribe to SSE bus and forward to WebSocket
    from backend.agent.sse_events import sse_bus
    async def forward_event(event):
        try:
            await ws.send_text(json.dumps(event.to_dict(), ensure_ascii=False))
        except Exception:
            pass
    sse_bus.subscribe(session_id, forward_event)

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "chat":
                _init_graph()
                sandbox_mode = msg.get("sandboxMode", "full-access")
                model = msg.get("model", "")
                user_text = msg.get("message","")
                # Send user message event
                await ws.send_text(json.dumps({
                    "type": "codex/event/user_message",
                    "data": {"content": user_text},
                }, ensure_ascii=False))
                try:
                    history = [{"role": h.get("role","user"), "content": h.get("content","")} for h in (msg.get("history") or [])]
                    state = await _graph.run(
                        user_text,
                        session_id=session_id,
                        workspace=msg.get("workspace","."),
                        sandbox_mode=sandbox_mode,
                        model=model,
                        history=history,
                    )
                    # Send final response
                    if state.final_response:
                        await ws.send_text(json.dumps({
                            "type": "codex/event/agent_message",
                            "data": {"content": state.final_response},
                        }, ensure_ascii=False))
                    await ws.send_text(json.dumps({
                        "type": "done",
                        "response": state.final_response,
                        "tokens": state.total_turns,
                    }, ensure_ascii=False))
                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    await ws.send_text(json.dumps({
                        "type": "codex/event/error",
                        "data": {"error": str(e), "traceback": tb[:500]},
                    }, ensure_ascii=False))
            elif msg.get("type") == "cancel":
                # Cancel handled by breaking the loop conceptually
                await ws.send_text(json.dumps({"type": "codex/event/turn_aborted", "data": {"reason": "user_cancelled"}}))
    except WebSocketDisconnect: pass
    finally:
        sse_bus.unsubscribe(session_id, forward_event)
        if session_id in _ws_connections: _ws_connections[session_id].remove(ws)

# Files
@router.get("/files")
async def list_files_api(path: str = "."):
    p = Path(path)
    if not p.exists(): raise HTTPException(404, "Not found")
    return {"path":str(p),"entries":[{"name":e.name,"isDirectory":e.is_dir(),"isFile":e.is_file()} for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))]}

