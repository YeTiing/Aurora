"""Aurora API - chat routes"""
from __future__ import annotations
import asyncio, json, time, uuid, os
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Any, Optional

from backend.thread_follower import ThreadFollower, ThreadSettings

from backend.api import deps as _deps
from backend.api.deps import cfg, llm, graph, rag, skills, plugins, ensure_all

router = APIRouter()
thread_follower = ThreadFollower()

from backend.api.models import ChatRequest, AgentResponse

from backend.config import config as _cfg_module
from backend.agent.llm_client import LLMClient, LLMConfig
from backend.api.path_security import resolve_allowed_path

def _build_full_prompt(message: str) -> str:
    """把 skills 触发结果与 RAG 检索上下文注入用户消息，返回完整提示词。

    REST 与 WS 四条入口必须共用同一实现：此前只有 REST 端点做了注入，
    而桌面端实际走 /ws/desktop（Electron ipcMain "agent:chat"），
    导致产品形态下技能与 RAG 上下文永远不生效。输出格式与旧 REST 实现保持逐字节一致。
    """
    ensure_all()
    skills_ctx = ""; rag_ctx = ""
    if _deps._skills:
        triggered = skills().match(message)
        skills_ctx = skills().inject(triggered)
    # 空向量库直接跳过检索：既省一次 embedding 调用，也沿用原有守卫语义
    if _deps._rag and rag().vector_store.count() > 0:
        chunks = rag().search(message, top_k=5, llm_client=_deps._llm)
        if chunks: rag_ctx = rag().format_context(chunks)
    return f"{skills_ctx}{rag_ctx}User: {message}" if (skills_ctx or rag_ctx) else message


# Shared lazy deps
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
    full = _build_full_prompt(req.message)
    history = [{"role": h.get("role","user"), "content": h.get("content","")} for h in (req.history or [])]
    from backend.session_registry import track
    track(sid, req.workspace)
    # 会话级图：token 预算/model/取消标记不能跨会话共享
    state = await _deps.get_graph_for(sid).run(full, session_id=sid, workspace=req.workspace, sandbox_mode=req.sandbox_mode, approval_mode=req.approval_mode, model=req.model, history=history, agent_role=req.agent_role, reasoning_effort=req.reasoning_effort)
    return AgentResponse(session_id=sid, response=state.final_response, plan=[p.to_dict() for p in state.plan], diffs=state.diffs)

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    sid = req.session_id or f"session_{uuid.uuid4().hex[:8]}"
    full = _build_full_prompt(req.message)
    history2 = [{"role": h.get("role","user"), "content": h.get("content","")} for h in (req.history or [])]
    from backend.session_registry import track
    track(sid, req.workspace)
    async def gen():
        async for chunk in _deps.get_graph_for(sid).run_with_stream(full, session_id=sid, workspace=req.workspace, sandbox_mode=req.sandbox_mode, approval_mode=req.approval_mode, model=req.model, history=history2, agent_role=req.agent_role, reasoning_effort=req.reasoning_effort):
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
                    # 注入必须在取 graph 之前：_build_full_prompt 内部会 ensure_all()
                    full = _build_full_prompt(user_text)
                    graph = _deps.get_graph_for(session_id)
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
                        full,
                        session_id=session_id,
                        workspace=workspace,
                        sandbox_mode=sandbox_mode,
                        approval_mode=msg.get("approvalMode", "on-request"),
                        model=model,
                        history=history,
                        agent_role=msg.get("agentRole", ""),
                        reasoning_effort=msg.get("reasoningEffort", "medium"),
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

            # Approval decisions from desktop
            if msg.get("type") == "approval_decision":
                from backend.approval import approval_bridge
                session_id = msg.get("sessionId") or "desktop"
                thread_id = msg.get("threadId") or session_id
                action = msg.get("action", "")
                if action not in ("approve", "deny"):
                    await ws.send_text(json.dumps({
                        "type": "codex/event/error",
                        "data": {"error": "Unsupported approval action"},
                        "session_id": session_id,
                        "thread_id": thread_id,
                    }, ensure_ascii=False))
                    continue
                result = await approval_bridge.decide(msg.get("requestId", ""), action, session_id, thread_id)
                if not result.get("ok"):
                    await ws.send_text(json.dumps({
                        "type": "codex/event/error",
                        "data": {"error": "Approval request not found", "request_id": msg.get("requestId", "")},
                        "session_id": session_id,
                        "thread_id": thread_id,
                    }, ensure_ascii=False))
                    continue
                await ws.send_text(json.dumps({
                    "type": "approval_decision_result",
                    "data": result,
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
                ensure_all()
                sandbox_mode = msg.get("sandboxMode", "full-access")
                model = msg.get("model", "")
                user_text = msg.get("message","")
                # Send user message event
                await ws.send_text(json.dumps({
                    "type": "codex/event/user_message",
                    "data": {"content": user_text},
                }, ensure_ascii=False))
                try:
                    # 与 REST / WS-desktop 保持同一注入路径（RAG + skills）
                    full = _build_full_prompt(user_text)
                    history = [{"role": h.get("role","user"), "content": h.get("content","")} for h in (msg.get("history") or [])]
                    state = await _deps.get_graph_for(session_id).run(
                        full,
                        session_id=session_id,
                        workspace=msg.get("workspace","."),
                        sandbox_mode=sandbox_mode,
                        approval_mode=msg.get("approvalMode", "on-request"),
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
                try:
                    # 取消后释放该会话的图；注意 cancel 本身保留检查点，
                    # 用户仍可经 /checkpoint/resume 续跑。
                    await _deps.get_graph_for(session_id).cancel(session_id)
                    _deps.drop_graph_for(session_id)
                except Exception:
                    pass
                await ws.send_text(json.dumps({"type": "codex/event/turn_aborted", "data": {"reason": "user_cancelled"}}))
    except WebSocketDisconnect: pass
    finally:
        sse_bus.unsubscribe(session_id, forward_event)
        if session_id in _ws_connections:
            _ws_connections[session_id].remove(ws)
            if not _ws_connections[session_id]:
                del _ws_connections[session_id]

def _resolve_workspace_path(path: str, workspace: str = ".") -> Path:
    ensure_all()
    return resolve_allowed_path(path, workspace, cfg())

# Files
@router.get("/files")
async def list_files_api(path: str = ".", workspace: str = "."):
    p = _resolve_workspace_path(path, workspace)
    if not p.exists() or not p.is_dir(): raise HTTPException(404, "Not found")
    return {"path":str(p),"entries":[{"name":e.name,"isDirectory":e.is_dir(),"isFile":e.is_file()} for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))]}

