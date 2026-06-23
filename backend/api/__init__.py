"""Aurora — FastAPI complete"""
from __future__ import annotations
import asyncio, json, time, uuid
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Any, Optional

app = FastAPI(title="Aurora AI Agent", version="0.2.0", docs_url="/docs", redoc_url="/redoc")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback; traceback.print_exc()
    return JSONResponse(status_code=500, content={"error": type(exc).__name__, "detail": str(exc)[:500]})

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"error": "HTTPException", "detail": exc.detail})

class ChatRequest(BaseModel):
    message: str; session_id: str | None = None; workspace: str = "."; stream: bool = False; sandbox_mode: str = "full-access"; model: str = ""

class AgentResponse(BaseModel):
    session_id: str; response: str; plan: list[dict] = []; diffs: list[str] = []; tokens: int = 0

class IndexRequest(BaseModel):
    path: str

class RenderPromptRequest(BaseModel):
    name: str; variables: dict = {}

class AutoPromptRequest(BaseModel):
    input: str

class ConfigUpdateRequest(BaseModel):
    key: str; value: Any

class SettingsUpdate(BaseModel):
    provider: str | None = None; model: str | None = None; api_key: str | None = None
    base_url: str | None = None; max_context_tokens: int | None = None
    max_turn_iter: int | None = None; temperature: float | None = None
    system_prompt: str | None = None

class LLMTestRequest(BaseModel):
    message: str = "Hello, say connected in one short sentence."

@app.middleware("http")
async def add_timing(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    response.headers["X-Process-Time"] = f"{(time.time()-start)*1000:.0f}ms"
    return response

_graph = None; _llm = None; _rag = None; _cfg = None; _skills = None; _plugins = None

def _init():
    global _cfg
    if _cfg is None:
        from backend.config import init_config, config
        _cfg = init_config(".")

def _init_llm():
    global _llm
    if _llm is None:
        _init()
        from backend.agent.llm_client import LLMClient, LLMConfig, MockLLMClient
        if _cfg.llm_api_key:
            _llm = LLMClient(LLMConfig(model=_cfg.llm_model, api_key=_cfg.llm_api_key, base_url=_cfg.llm_base_url))
        else:
            _llm = MockLLMClient()

def _init_graph():
    global _graph
    if _graph is None:
        _init_llm(); _init()
        from backend.agent.graph import AgentGraph
        from backend.tools import tool_registry
        async def tool_handler(name, args, ws):
            result = await tool_registry.execute(name, args, ws)
            return {"success": result.success, "output": result.output, "error": result.error}
        _graph = AgentGraph(llm=_llm, tool_handler=tool_handler,
                           tools_schema=tool_registry.list_tools_openai(),
                           max_turns=_cfg.max_turn_iter, workspace=".")

def _init_rag():
    global _rag
    if _rag is None:
        from backend.rag import rag_engine, init_rag
        _rag = rag_engine

def _init_skills():
    global _skills
    if _skills is None:
        from backend.skills import skill_manager
        _skills = skill_manager

def _init_plugins():
    global _plugins
    if _plugins is None:
        from backend.plugins import plugin_manager
        _plugins = plugin_manager



# === SOUL.md Personality ===
@app.get("/soul")
async def soul_get():
    """Get current SOUL.md personality."""
    from pathlib import Path
    sp = Path(".aurora") / "SOUL.md"
    if sp.exists():
        return {"content": sp.read_text(encoding="utf-8"), "path": str(sp)}
    return {"content": "", "path": str(sp)}

@app.post("/soul")
async def soul_update(req: dict):
    """Update SOUL.md personality."""
    from pathlib import Path
    sp = Path(".aurora") / "SOUL.md"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(req.get("content", ""), encoding="utf-8")
    return {"updated": True, "path": str(sp)}

# Health
@app.get("/health")
async def health():
    return {"status":"ok","version":"0.2.0","timestamp":time.time()}

# Chat
@app.post("/chat")
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
    state = await _graph.run(full, session_id=sid, workspace=req.workspace)
    return AgentResponse(session_id=sid, response=state.final_response, plan=[p.to_dict() for p in state.plan], diffs=state.diffs)

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    sid = req.session_id or f"session_{uuid.uuid4().hex[:8]}"
    _init_graph(); _init_skills(); _init_rag()
    full = req.message
    async def gen():
        async for chunk in _graph.run_with_stream(full, session_id=sid, workspace=req.workspace):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\
\
"
    return StreamingResponse(gen(), media_type="text/event-stream")

# WebSocket
_ws_connections: dict[str, list[WebSocket]] = {}

@app.websocket("/ws/{session_id}")
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
                    state = await _graph.run(
                        user_text,
                        session_id=session_id,
                        workspace=msg.get("workspace","."),
                        sandbox_mode=sandbox_mode,
                        model=model,
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
@app.get("/files")
async def list_files_api(path: str = "."):
    p = Path(path)
    if not p.exists(): raise HTTPException(404, "Not found")
    return {"path":str(p),"entries":[{"name":e.name,"isDirectory":e.is_dir(),"isFile":e.is_file()} for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))]}

@app.get("/files/read")
async def read_file(path: str):
    p = Path(path)
    if not p.exists(): raise HTTPException(404, "Not found")
    return {"path":str(p),"content":p.read_text(encoding="utf-8", errors="ignore")}

@app.post("/files/write")
async def write_file(req: dict):
    p = Path(req["path"]); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.get("content",""), encoding="utf-8")
    return {"path":str(p),"written":True}

@app.post("/files/search")
async def search_files(req: dict):
    import subprocess
    try:
        r = subprocess.run(["rg","--line-number","--max-count",str(req.get("max_results",50)),req.get("query",""),str(req.get("path","."))], capture_output=True, text=True, timeout=10)
        return {"results":r.stdout[:10000],"count":len(r.stdout.splitlines())}
    except: return {"results":"","count":0}

# RAG
@app.post("/rag/index")
async def index_project(req: IndexRequest):
    _init_rag(); import os
    files = []
    for dirpath, dirnames, filenames in os.walk(req.path):
        dirnames[:] = [d for d in dirnames if d not in (".git","node_modules","__pycache__","venv",".venv")]
        for f in filenames: files.append(os.path.join(dirpath, f))
    _rag.index_files(files)
    return {"indexed":len(files)}

@app.get("/rag/search")
async def search_rag(query: str, top_k: int = 5):
    _init_rag()
    chunks = _rag.search(query, top_k=top_k, llm_client=_llm)
    return {"count":len(chunks),"results":[{"content":c["content"][:200],"file":c["metadata"].get("file","")} for c in chunks]}

# Sessions
_sessions_meta: dict[str, dict] = {}
@app.get("/sessions")
async def list_sessions():
    return {"sessions":list(_sessions_meta.values()),"ws_connections":sum(len(v) for v in _ws_connections.values())}

# Tools
@app.get("/tools")
async def list_tools():
    from backend.tools import tool_registry
    return {"tools":[{"name":s.name,"description":s.description,"category":s.category} for s in tool_registry.list_tools()],"stats":tool_registry.stats()}

# Config
@app.get("/config")
async def get_config(): _init(); return _cfg.all()

@app.post("/config")
async def update_config(req: ConfigUpdateRequest): _init(); return {"key":req.key,"current":_cfg.get(req.key)}

# Plugins
@app.get("/plugins")
async def list_plugins(): _init_plugins(); return {"plugins":_plugins.list()}

@app.post("/plugins/{name}/load")
async def load_plugin(name: str): _init_plugins(); return {"name":name,"loaded":_plugins.load(name)}

@app.post("/plugins/{name}/unload")
async def unload_plugin(name: str): _init_plugins(); return {"name":name,"unloaded":_plugins.unload(name)}

@app.post("/plugins/{name}/reload")
async def reload_plugin(name: str): _init_plugins(); return {"name":name,"reloaded":_plugins.reload(name)}

# Prompts
@app.get("/prompts")
async def list_prompts(category: str|None=None, search: str|None=None):
    from backend.prompt_templates import prompt_manager
    if search: tpls = prompt_manager.search(search)
    elif category: tpls = prompt_manager.by_category(category)
    else: tpls = list(prompt_manager.templates.values())
    return {"count":len(tpls),"templates":[{"name":t.name,"description":t.description,"category":t.category} for t in tpls]}

@app.post("/prompts/render")
async def render_prompt(req: RenderPromptRequest):
    from backend.prompt_templates import prompt_manager
    result = prompt_manager.render(req.name, **req.variables)
    if result.startswith("Template"): raise HTTPException(404, result)
    return {"rendered":result}

# Multi-Agent
@app.get("/agents/tree")
async def agent_tree():
    from backend.multi_agent import orchestrator; return orchestrator.get_tree()

@app.get("/agents/stats")
async def agent_stats():
    from backend.multi_agent import orchestrator; return orchestrator.stats()

# Context
@app.get("/context/stats")
async def context_stats():
    from backend.context import tracker; return tracker.summary()

# MCP
@app.get("/mcp/servers")
async def list_mcp_servers():
    from backend.mcp_hub import mcp_hub; return {"servers": mcp_hub.list_servers()}

@app.post("/mcp/servers/start")
async def start_mcp_server(req: dict):
    from backend.mcp_hub import mcp_hub, MCPServerConfig
    cfg = MCPServerConfig(**{k: req.get(k,v) for k,v in MCPServerConfig.__dataclass_fields__.items() if k in req or hasattr(MCPServerConfig,k)})
    return {"name":cfg.name,"started":await mcp_hub.start_server(cfg)}

@app.post("/mcp/servers/{name}/stop")
async def stop_mcp_server(name: str):
    from backend.mcp_hub import mcp_hub; await mcp_hub.stop_server(name); return {"name":name,"stopped":True}

# Processes
@app.get("/processes")
async def list_processes():
    from backend.process_manager import process_manager
    procs = process_manager.list_all()
    return {"processes":[{"id":p.id,"command":p.command,"status":p.status} for p in procs],"stats":process_manager.stats()}

@app.post("/processes/{proc_id}/kill")
async def kill_process(proc_id: str):
    from backend.process_manager import process_manager
    return {"id":proc_id,"killed":process_manager.kill(proc_id)}

# Storage
@app.get("/storage/stats")
async def storage_stats():
    from backend.sqlite_persistence import get_thread_goals_db, get_logs_db, get_memories_db
    return {"goals":get_thread_goals_db().stats(),"memories":len(get_memories_db().list_keys())}

@app.get("/storage/memories")
async def list_memories(category: str|None=None):
    from backend.sqlite_persistence import get_memories_db
    db = get_memories_db(); keys = db.list_keys(category)
    return {"count":len(keys),"memories":{k:db.get(k) for k in keys[:50]}}

# ═══════════ Settings / Models / LLM Test ═══════════

@app.get("/settings")
async def get_settings():
    _init()
    key = _cfg.get("llm.api_key", "")
    return {
        "provider": _cfg.get("llm.provider", "openai"),
        "model": _cfg.get("llm.model", "gpt-4o"),
        "api_key": (key[:8] + "***") if key else "",
        "base_url": _cfg.get("llm.base_url", "https://api.openai.com/v1"),
        "max_context_tokens": _cfg.get("context.max_tokens", 24000),
        "max_turn_iter": _cfg.get("agent.max_turn_iter", 30),
        "temperature": _cfg.get("llm.temperature", 0.3),
        "context_window": _cfg.model_context_window,
        "compaction_threshold": _cfg.get("context.compaction_threshold", 0.85),
    }

@app.post("/settings")
async def update_settings(req: SettingsUpdate):
    _init()
    config_path = Path.home() / ".aurora" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if config_path.exists():
        try: existing = json.loads(config_path.read_text(encoding="utf-8"))
        except: pass
    llm_updates = {}
    if req.provider: llm_updates["provider"] = req.provider
    if req.model: llm_updates["model"] = req.model
    if req.api_key: llm_updates["api_key"] = req.api_key
    if req.base_url: llm_updates["base_url"] = req.base_url
    if req.temperature is not None: llm_updates["temperature"] = req.temperature
    if llm_updates: existing.setdefault("llm", {}).update(llm_updates)
    if req.max_context_tokens: existing.setdefault("context", {}).update({"max_tokens": req.max_context_tokens})
    if req.max_turn_iter: existing.setdefault("agent", {}).update({"max_turn_iter": req.max_turn_iter})
    if req.system_prompt is not None: existing["system_prompt_custom"] = req.system_prompt
    config_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    global _cfg, _llm, _graph
    _cfg = None; _llm = None; _graph = None
    _init(); _init_llm(); _init_graph()
    return {"ok": True, "updated": list(llm_updates.keys())}

@app.get("/models")
async def list_models():
    _init()
    from backend.model_discovery import model_discovery as md
    base_url = _cfg.get("llm.base_url", "https://api.openai.com/v1")
    api_key = _cfg.get("llm.api_key", "")
    models = await md.list_models(base_url, api_key)
    return {"count":len(models),"models":[{"id":m.id,"max_tokens":m.max_tokens,"provider":m.provider} for m in models],"base_url":base_url}

@app.get("/models/context")
async def model_context_info(model: str = ""):
    _init()
    from backend.model_discovery import model_discovery as md
    m = model or _cfg.get("llm.model", "gpt-4o")
    return md.get_context_limit(m, _cfg.get("context.max_tokens", 0))

@app.post("/llm/test")
async def test_llm(req: LLMTestRequest = LLMTestRequest()):
    _init_llm()
    try:
        resp = await _llm.chat([{"role":"user","content":req.message}], max_tokens=50, temperature=0.1)
        return {"ok":True,"response":resp.content[:200],"model":resp.model or "","tokens":resp.total_tokens or 0}
    except Exception as e:
        return {"ok":False,"error":str(e)[:300]}


# ═══════════ Browser Use ═══════════
@app.get("/browser/pages")
async def browser_list_pages():
    from backend.browser_use import browser_use
    pages = await browser_use.list_pages()
    return {"pages": [{"url": p.url, "title": p.title, "id": p.target_id} for p in pages]}

@app.post("/browser/navigate")
async def browser_navigate(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.navigate(req.get("url", "https://google.com"))
    return {"ok": result.get("error") is None, "result": result}

@app.post("/browser/screenshot")
async def browser_screenshot():
    from backend.browser_use import browser_use
    result = await browser_use.screenshot()
    return {"ok": result.get("error") is None, **result}

@app.post("/browser/click")
async def browser_click(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.click(req.get("selector", "body"))
    return {"ok": result.get("error") is None, "result": result}

@app.post("/browser/type")
async def browser_type(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.type_text(req.get("selector", "input"), req.get("text", ""))
    return {"ok": True, "result": result}

# ═══════════ Approval ═══════════
@app.get("/approval/status")
async def approval_status():
    from backend.approval import approval_manager
    return approval_manager.stats()

@app.get("/approval/pending")
async def approval_pending():
    from backend.approval import approval_manager
    pending = approval_manager.get_pending()
    return {"count": len(pending), "requests": [{"id": r.id, "type": r.type, "risk": r.risk_level.value, "description": r.description} for r in pending]}

class ApprovalAction(BaseModel):
    request_id: str
    action: str  # approve / deny

@app.post("/approval/{action}")
async def approval_act(action: str, req: ApprovalAction):
    from backend.approval import approval_manager
    if action == "approve":
        ok = approval_manager.approve(req.request_id)
    else:
        ok = approval_manager.deny(req.request_id)
    return {"request_id": req.request_id, "action": action, "ok": ok}

# ═══════════ Threads ═══════════
@app.get("/threads")
async def list_threads(status: str = ""):
    from backend.thread_manager import thread_manager
    threads = thread_manager.list_threads(status)
    return {"count": len(threads), "threads": [{"id": t.id, "title": t.title, "status": t.status, "model": t.model, "reasoning_effort": t.reasoning_effort} for t in threads]}

class CreateThreadRequest(BaseModel):
    title: str = ""; workspace: str = ""; model: str = ""; reasoning_effort: str = "medium"; thread_source: str = "user"

@app.post("/threads")
async def create_thread(req: CreateThreadRequest):
    from backend.thread_manager import thread_manager
    t = thread_manager.create(title=req.title, workspace=req.workspace, model=req.model, reasoning_effort=req.reasoning_effort, thread_source=req.thread_source)
    return {"id": t.id, "title": t.title, "created": True}

@app.post("/threads/{thread_id}/fork")
async def fork_thread(thread_id: str, req: dict = {}):
    from backend.thread_manager import thread_manager
    t = thread_manager.fork(thread_id, req.get("title", ""))
    if not t: raise HTTPException(404, "Thread not found")
    return {"id": t.id, "title": t.title, "forked_from": thread_id}

@app.get("/threads/{thread_id}")
async def read_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    t = thread_manager.read(thread_id)
    if not t: raise HTTPException(404, "Not found")
    return {"id": t.id, "title": t.title, "status": t.status, "workspace": t.workspace, "model": t.model, "reasoning_effort": t.reasoning_effort, "parent_id": t.parent_id}

@app.post("/threads/{thread_id}/pin")
async def pin_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_pinned(thread_id)
    return {"id": thread_id, "pinned": ok}

@app.post("/threads/{thread_id}/archive")
async def archive_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_archived(thread_id)
    return {"id": thread_id, "archived": ok}

@app.post("/threads/{thread_id}/title")
async def rename_thread(thread_id: str, req: dict):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_title(thread_id, req.get("title", ""))
    return {"id": thread_id, "renamed": ok}

# ═══════════ Heartbeat ═══════════
@app.get("/heartbeat/status")
async def heartbeat_status():
    from backend.heartbeat import heartbeat_manager
    return {"enabled": heartbeat_manager._enabled, "interval": heartbeat_manager._config.interval_seconds}

@app.post("/heartbeat/send")
async def heartbeat_send(req: dict):
    from backend.heartbeat import heartbeat_manager
    msg = heartbeat_manager.notify(req.get("message", "Checking in"))
    return {"heartbeat": msg[:200]}

# ═══════════ Sentry ═══════════
class SentryConfig(BaseModel):
    dsn: str = ""; enabled: bool = True

@app.post("/sentry/configure")
async def configure_sentry(req: SentryConfig):
    try:
        import sentry_sdk
        if req.enabled and req.dsn:
            sentry_sdk.init(dsn=req.dsn, traces_sample_rate=1.0)
            return {"ok": True, "message": "Sentry configured"}
        return {"ok": False, "message": "DSN required"}
    except ImportError:
        return {"ok": False, "message": "sentry-sdk not installed. pip install sentry-sdk"}

@app.post("/sentry/test")
async def test_sentry():
    try:
        import sentry_sdk
        sentry_sdk.capture_message("Aurora Sentry test message", level="info")
        return {"ok": True, "message": "Test message sent"}
    except ImportError:
        return {"ok": False, "message": "sentry-sdk not installed"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}

# ═══════════ AGENTS.md ═══════════
@app.get("/agents-md")
async def list_agents_md():
    from backend.agents_md import agents_loader
    rules = agents_loader.scan(".")
    return {"count": len(rules), "files": [str(r.file_path) for r in rules]}

@app.get("/agents-md/read")
async def read_agents_md(path: str = "."):
    from backend.agents_md import agents_loader
    content = agents_loader.inject(path)
    return {"path": path, "has_instructions": bool(content), "content": content[:3000]}


# ── File Upload ──
@app.post("/files/upload")
async def upload_file(file: UploadFile = File(...), workspace: str = Form(".")):
    """Upload a file to the workspace uploads directory."""
    if not file.filename:
        raise HTTPException(400, "No file provided")
    ws_dir = Path(workspace) / "uploads"
    ws_dir.mkdir(parents=True, exist_ok=True)
    file_path = ws_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)
    mime_type, _ = mimetypes.guess_type(file.filename)
    mime_type = mime_type or "application/octet-stream"
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
    ext = file_path.suffix.lower()
    is_image = ext in image_exts
    result = {
        "filename": file.filename,
        "path": str(file_path),
        "size": file_path.stat().st_size,
        "mime_type": mime_type,
        "is_image": is_image,
    }
    if is_image:
        b64 = base64.b64encode(content).decode("ascii")
        result["data_url"] = f"data:{mime_type};base64,{b64}"
    return result

# ── Diff Engine ──
class DiffMergeRequest(BaseModel):
    base: str; ours: str; theirs: str

@app.get("/diff")
async def get_diff(file: str = ""):
    """Get diff of a working file vs its last saved state."""
    from backend.tools.diff_engine import diff_engine
    path = Path(file)
    if not path.exists():
        raise HTTPException(404, f"File not found: {file}")
    current = path.read_text(encoding="utf-8", errors="ignore")
    git_dir = None
    p = path.parent
    for _ in range(10):
        if (p / ".git").exists():
            git_dir = p
            break
        if p.parent == p:
            break
        p = p.parent
    original = ""
    if git_dir:
        import subprocess
        try:
            rel = path.relative_to(git_dir)
            r = subprocess.run(["git", "-C", str(git_dir), "show", f"HEAD:{rel}"],
                             capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                original = r.stdout
        except Exception:
            pass
    if not original and path.exists():
        original = current
    hunks = diff_engine.compute_diff(original, current)
    has_changes = any(
        any(l.startswith("+") or l.startswith("-") for l in h["lines"] if not l.startswith(" "))
        for h in hunks
    )
    return {"file": file, "has_changes": has_changes, "hunks": hunks, "hunk_count": len(hunks)}

@app.post("/diff/merge")
async def merge_diff(req: DiffMergeRequest):
    """Three-way merge with conflict markers."""
    from backend.tools.diff_engine import diff_engine
    merged, conflicts = diff_engine.three_way_merge(req.base, req.ours, req.theirs)
    return {
        "merged": merged,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "has_conflicts": len(conflicts) > 0
    }

# ── Checkpoint Undo/Redo ──
@app.post("/checkpoint")
async def save_checkpoint(req: dict = {}):
    """Save current workspace state as a checkpoint."""
    from backend.agent.checkpoint import CheckpointManager
    mgr = CheckpointManager()
    cid = mgr.save_workspace_state(req.get("label", ""))
    return {"checkpoint_id": cid, "saved": True, "label": req.get("label", "")}

@app.post("/checkpoint/undo")
async def undo_checkpoint():
    """Undo last checkpoint action."""
    from backend.agent.checkpoint import CheckpointManager
    mgr = CheckpointManager()
    cid = mgr.undo()
    if cid is None:
        return {"undone": False, "message": "Nothing to undo"}
    return {"undone": True, "checkpoint_id": cid}

@app.post("/checkpoint/redo")
async def redo_checkpoint():
    """Redo last undone checkpoint action."""
    from backend.agent.checkpoint import CheckpointManager
    mgr = CheckpointManager()
    cid = mgr.redo()
    if cid is None:
        return {"redone": False, "message": "Nothing to redo"}
    return {"redone": True, "checkpoint_id": cid}

@app.get("/checkpoint/list")
async def list_checkpoints():
    """List checkpoint history."""
    from backend.agent.checkpoint import CheckpointManager
    mgr = CheckpointManager()
    history = mgr.list_history()
    undo_count = sum(1 for h in history if h["type"] == "undo_stack")
    redo_count = sum(1 for h in history if h["type"] == "redo_stack")
    return {"history": history, "count": len(history), "undo_count": undo_count, "redo_count": redo_count}

# ── Task Queue ──
class TaskSubmitRequest(BaseModel):
    type: str = "rag_index"  # rag_index, llm_test
    path: str = ""
    message: str = "Hello, say connected in one short sentence."

@app.post("/tasks")
async def submit_task(req: TaskSubmitRequest):
    """Submit a background task."""
    from backend.task_queue import task_queue as tq
    from backend.api import _init_rag, _init_llm, _rag, _llm
    if req.type == "rag_index":
        _init_rag()
        import os as _os
        async def index_task(p):
            files = []
            for dirpath, dirnames, filenames in _os.walk(p):
                dirnames[:] = [d for d in dirnames if d not in (".git","node_modules","__pycache__","venv",".venv")]
                for fn in filenames:
                    files.append(_os.path.join(dirpath, fn))
            _rag.index_files(files)
            return f"Indexed {len(files)} files"
        task_id = await tq.submit(index_task, req.path, name="rag_index")
        return {"task_id": task_id, "type": "rag_index", "status": "pending"}
    elif req.type == "llm_test":
        _init_llm()
        async def llm_test_task(msg):
            resp = await _llm.chat([{"role":"user","content":msg}], max_tokens=50, temperature=0.1)
            return resp.content[:200]
        task_id = await tq.submit(llm_test_task, req.message, name="llm_test")
        return {"task_id": task_id, "type": "llm_test", "status": "pending"}
    else:
        raise HTTPException(400, f"Unknown task type: {req.type}")

@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get task status and result."""
    from backend.task_queue import task_queue as tq
    status = tq.status(task_id)
    if status is None:
        raise HTTPException(404, f"Task not found: {task_id}")
    return status

@app.get("/tasks")
async def list_tasks():
    """List all background tasks."""
    from backend.task_queue import task_queue as tq
    return {"tasks": tq.list_all(), "stats": tq.stats()}

@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running or pending task."""
    from backend.task_queue import task_queue as tq
    ok = tq.cancel(task_id)
    return {"task_id": task_id, "cancelled": ok}

# ── Prompt Presets ──
@app.get("/presets")
async def list_presets(search: str = ""):
    """List prompt presets, optionally search by keyword."""
    from backend.prompt_presets import preset_manager as pm
    if search:
        results = pm.search(search)
        return {"count": len(results), "presets": results}
    return {"count": len(pm.presets), "presets": pm.list_all(), "categories": pm.categories()}

@app.get("/presets/{category}")
async def list_presets_by_category(category: str):
    """List prompt presets by category."""
    from backend.prompt_presets import preset_manager as pm
    presets = pm.list_by_category(category)
    return {"category": category, "count": len(presets), "presets": presets}

@app.post("/presets/render")
async def render_preset(req: dict):
    """Render a prompt preset with variables."""
    from backend.prompt_presets import preset_manager as pm
    preset_id = req.get("id", req.get("preset_id", ""))
    vars_ = {k: v for k, v in req.items() if k not in ("id", "preset_id")}
    rendered = pm.render(preset_id, **vars_)
    if rendered.startswith("Preset"):
        raise HTTPException(404, rendered)
    return {"preset_id": preset_id, "rendered": rendered}

# ══ Session Rollout ══

@app.get("/sessions")
async def list_sessions():
    from backend.session_rollout import RolloutReader
    sessions = RolloutReader.list_sessions()
    return {"count": len(sessions), "sessions": sessions}

@app.get("/sessions/{session_id}/rollout")
async def get_session_rollout(session_id: str):
    from pathlib import Path
    from fastapi.responses import FileResponse
    from backend.session_rollout import _rollout_writers, RolloutReader
    sessions = RolloutReader.list_sessions()
    for s in sessions:
        if s.get("session_id") == session_id:
            fp = s.get("file", "")
            if fp and Path(fp).exists():
                return FileResponse(fp, media_type="application/x-ndjson",
                    filename=f"rollout-{session_id}.jsonl")
    raise HTTPException(404, f"No rollout found for session {session_id}")

@app.get("/sessions/{session_id}/stats")
async def get_session_stats(session_id: str):
    from backend.session_rollout import RolloutReader
    sessions = RolloutReader.list_sessions()
    for s in sessions:
        if s.get("session_id") == session_id:
            return s
    raise HTTPException(404, f"No stats found for session {session_id}")

# ══ Goal System ══

class GoalCreateRequest(BaseModel):
    objective: str
    token_budget: int | None = None

class GoalUpdateRequest(BaseModel):
    status: str  # "complete" or "blocked"

@app.post("/goal")
async def create_goal(req: GoalCreateRequest):
    from backend.goal import goal_manager
    goal = goal_manager.create_goal(req.objective, req.token_budget)
    return {"goal": goal.to_dict()}

@app.put("/goal/{goal_id}")
async def update_goal(goal_id: str, req: GoalUpdateRequest):
    from backend.goal import goal_manager
    if req.status not in ("complete", "blocked"):
        raise HTTPException(400, "Status must be 'complete' or 'blocked'")
    goal = goal_manager.update_goal(goal_id, req.status)
    if goal is None:
        raise HTTPException(404, f"Goal not found: {goal_id}")
    return {"goal": goal.to_dict()}

@app.get("/goal")
async def get_goal():
    from backend.goal import goal_manager
    return goal_manager.stats()

# ══ Context Budget ══

@app.get("/context/budget")
async def get_context_budget():
    from backend.context import TokenAllocationBudget, tracker
    budget = TokenAllocationBudget()
    used = {"system": 1500, "tools": 2000, "rag": 0, "conversation": tracker.stats.total_prompt}
    return {
        "budget": {
            "total": budget.total,
            "system_prompt": budget.system_prompt,
            "tool_specs": budget.tool_specs,
            "conversation_history": budget.conversation_history,
            "output_reserve": budget.output_reserve,
        },
        "used": used,
        "available": budget.available(used),
        "tracker": tracker.summary(),
    }

# ========== Auth Routes ==========
@app.post("/auth/login")
async def auth_login(req: dict):
    from backend.auth import auth_manager
    api_key = req.get("api_key", req.get("key", ""))
    name = req.get("name", "default")
    if not api_key:
        raise HTTPException(400, "api_key is required")
    state = auth_manager.login_api_key(name, api_key)
    return auth_manager.get_active_auth()

@app.get("/auth/oauth/login")
async def auth_oauth_login(provider: str = "openai", redirect_uri: str = "http://localhost:8000/auth/oauth/callback"):
    from backend.auth import auth_manager
    result = auth_manager.start_oauth_flow(provider, redirect_uri)
    return result

@app.get("/auth/oauth/callback")
async def auth_oauth_callback(code: str, state: str):
    import httpx
    from backend.auth import auth_manager
    async with httpx.AsyncClient(timeout=30.0) as client:
        result = await auth_manager.complete_oauth_flow(code, state, client)
    return auth_manager.get_active_auth()

@app.get("/auth/status")
async def auth_status():
    from backend.auth import auth_manager
    return auth_manager.get_active_auth()

@app.post("/auth/logout")
async def auth_logout():
    from backend.auth import auth_manager
    return auth_manager.logout()

# ========== Tool Call Blocks Debug ==========
@app.get("/tools/blocks")
async def tools_blocks():
    from backend.agent.tool_call_aggregator import tool_call_aggregator
    return tool_call_aggregator.get_snapshot()

@app.post("/tools/blocks/reset")
async def tools_blocks_reset():
    from backend.agent.tool_call_aggregator import tool_call_aggregator
    tool_call_aggregator.reset()
    return {"reset": True, "snapshot": tool_call_aggregator.get_snapshot()}

# ========== Model Discovery ==========
@app.post("/models/discover")
async def models_discover(req: dict):
    from backend.model_discovery import model_discovery
    provider = req.get("provider", "openai")
    api_key = req.get("api_key", "")
    base_url = req.get("base_url", "")
    if provider == "openai":
        models = await model_discovery.discover_openai_models(api_key, base_url or "https://api.openai.com/v1")
    elif provider == "ollama":
        models = await model_discovery.discover_ollama_models(base_url or "http://localhost:11434")
    elif provider == "groq":
        models = await model_discovery.discover_groq_models(api_key)
    elif provider == "deepseek":
        models = await model_discovery.discover_deepseek_models(api_key)
    else:
        raise HTTPException(400, f"Unknown provider: {provider}")
    result = []
    for m in models:
        result.append({
            "id": m.id, "provider": m.provider, "owned_by": m.owned_by,
            "context_window": m.context_window, "reasoning_support": m.reasoning_support,
            "vision_support": m.vision_support, "function_calling": m.function_calling,
            "streaming": m.streaming, "input_price": m.input_price,
            "output_price": m.output_price, "speed_tier": m.speed_tier,
            "recommended_for": m.recommended_for, "available": m.available,
        })
    return {"provider": provider, "count": len(result), "models": result}

@app.post("/models/discover/all")
async def models_discover_all(req: dict):
    from backend.model_discovery import model_discovery
    models = await model_discovery.discover_all(
        api_key=req.get("openai_key", req.get("api_key", "")),
        ollama_url=req.get("ollama_url", "http://localhost:11434"),
        groq_key=req.get("groq_key", ""),
        deepseek_key=req.get("deepseek_key", ""),
    )
    result = []
    for m in models:
        result.append({
            "id": m.id, "provider": m.provider, "context_window": m.context_window,
            "speed_tier": m.speed_tier, "recommended_for": m.recommended_for,
            "input_price": m.input_price, "output_price": m.output_price,
        })
    return {"count": len(result), "models": result}

@app.get("/models/benchmarks")
async def models_benchmarks():
    from backend.model_discovery import model_discovery
    benchmarks = {}
    for key, br in model_discovery._benchmarks.items():
        benchmarks[key] = {
            "model_id": br.model_id, "provider": br.provider,
            "latency_ms": br.latency_ms, "tokens_per_sec": br.tokens_per_sec,
            "input_tokens": br.input_tokens, "output_tokens": br.output_tokens,
            "success": br.success, "error": br.error,
        }
    return {"count": len(benchmarks), "benchmarks": benchmarks, "cache_age": model_discovery.get_cache_age()}

@app.post("/models/test")
async def models_test(req: dict):
    from backend.model_discovery import model_discovery
    provider = req.get("provider", "openai")
    model = req.get("model", "gpt-4o")
    api_key = req.get("api_key", "")
    base_url = req.get("base_url", "")
    result = await model_discovery.test_model(provider, model, api_key, base_url)
    return {
        "model_id": result.model_id, "provider": result.provider,
        "latency_ms": result.latency_ms, "tokens_per_sec": result.tokens_per_sec,
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "success": result.success, "error": result.error,
    }

@app.get("/models/recommend")
async def models_recommend(type: str = "code"):
    from backend.model_discovery import model_discovery
    return model_discovery.recommend(type)

@app.post("/models/cache/purge")
async def models_cache_purge():
    from backend.model_discovery import model_discovery
    removed = model_discovery.cache_results(ttl_seconds=0)
    return {"removed": removed, "remaining_cache": model_discovery.get_cache_age()}


# === Cron Scheduler ===
@app.get("/cron")
async def cron_list():
    """List all cron tasks."""
    from backend.cron_scheduler import get_cron
    return get_cron().list_tasks()

@app.post("/cron")
async def cron_add(req: dict):
    """Add a cron task: {name, schedule, prompt}."""
    from backend.cron_scheduler import get_cron
    ok, msg = get_cron().add(
        req.get("name", ""),
        req.get("schedule", ""),
        req.get("prompt", ""),
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@app.delete("/cron/{name}")
async def cron_remove(name: str):
    """Remove a cron task."""
    from backend.cron_scheduler import get_cron
    ok = get_cron().remove(name)
    if not ok:
        raise HTTPException(404, f"Task not found: {name}")
    return {"removed": name}

@app.post("/cron/{name}/toggle")
async def cron_toggle(name: str):
    """Toggle a cron task on/off."""
    from backend.cron_scheduler import get_cron
    enabled = get_cron().toggle(name)
    return {"name": name, "enabled": enabled}

@app.get("/cron/stats")
async def cron_stats():
    """Get cron scheduler stats."""
    from backend.cron_scheduler import get_cron
    return get_cron().stats()

# === Observability Stats ===
@app.get("/observability/stats")
async def observability_stats():
    from backend.observability.stats import get_stats
    return get_stats()


# === Dual-File Memory (Hermes-style) ===
@app.get("/memory/agent")
async def memory_agent_list():
    """List agent memory entries."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.agent_memory.list_entries()

@app.get("/memory/agent/stats")
async def memory_agent_stats():
    """Get agent memory stats."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.agent_memory.stats()

@app.post("/memory/agent")
async def memory_agent_add(req: dict):
    """Add an entry to agent memory."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.agent_memory.add(req.get("text", ""), source="api")
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@app.put("/memory/agent/{index}")
async def memory_agent_replace(index: int, req: dict):
    """Replace an entry in agent memory."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.agent_memory.replace(index, req.get("text", ""), source="api")
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@app.delete("/memory/agent/{index}")
async def memory_agent_remove(index: int):
    """Remove an entry from agent memory."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.agent_memory.remove(index)
    if not ok:
        raise HTTPException(404, msg)
    return {"success": True, "message": msg}

@app.get("/memory/user")
async def memory_user_list():
    """List user profile entries."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.user_profile.list_entries()

@app.get("/memory/user/stats")
async def memory_user_stats():
    """Get user profile stats."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.user_profile.stats()

@app.post("/memory/user")
async def memory_user_add(req: dict):
    """Add an entry to user profile."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.user_profile.add(req.get("text", ""), source="api")
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@app.put("/memory/user/{index}")
async def memory_user_replace(index: int, req: dict):
    """Replace an entry in user profile."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.user_profile.replace(index, req.get("text", ""), source="api")
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@app.delete("/memory/user/{index}")
async def memory_user_remove(index: int):
    """Remove an entry from user profile."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    ok, msg = dm.user_profile.remove(index)
    if not ok:
        raise HTTPException(404, msg)
    return {"success": True, "message": msg}

@app.get("/memory/stats")
async def memory_stats():
    """Get full memory system stats."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    return dm.stats()

@app.post("/memory/curator/run")
async def memory_curator_run():
    """Run lightweight curator deduplication."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    result = dm.curator.run_lightweight()
    return {"success": True, "result": result}

@app.post("/memory/session/{session_id}/end")
async def memory_session_end(session_id: str, req: dict):
    """End session: run curator, index summary."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    result = dm.end_session(session_id, req.get("summary", ""))
    return {"success": True, "result": result}

@app.get("/memory/sessions/search")
async def memory_sessions_search(q: str = ""):
    """Search past sessions."""
    _init()
    from backend.dual_memory import get_dual_memory
    dm = get_dual_memory()
    results = dm.search_past_sessions(q)
    return {"query": q, "count": len(results), "results": results}

# === Semantic Memory ===
class SemanticIndexRequest(BaseModel):
    text: str
    metadata: dict | None = None

@app.post("/memory/semantic/index")
async def semantic_index(req: SemanticIndexRequest):
    from backend.memory.semantic import get_semantic_memory
    sm = get_semantic_memory()
    mid = sm.index(req.text, req.metadata)
    return {"id": mid, "indexed": True}

@app.get("/memory/semantic/search")
async def semantic_search(q: str = "", k: int = 5):
    from backend.memory.semantic import get_semantic_memory
    sm = get_semantic_memory()
    results = sm.search(q, top_k=k)
    return {"query": q, "count": len(results), "results": results}

@app.delete("/memory/semantic/{memory_id}")
async def semantic_forget(memory_id: str):
    from backend.memory.semantic import get_semantic_memory
    sm = get_semantic_memory()
    ok = sm.forget(memory_id)
    if not ok:
        raise HTTPException(404, f"Memory not found: {memory_id}")
    return {"id": memory_id, "forgotten": True}

@app.post("/memory/semantic/build-episodic")
async def semantic_build_episodic():
    from backend.memory.semantic import get_semantic_memory
    sm = get_semantic_memory()
    count = sm.build_episodic_index()
    return {"indexed_episodes": count}


# === Skills ===
@app.get("/memory/skills")
async def memory_skills_list():
    _init()
    from backend.dual_memory import get_closed_loop
    return get_closed_loop().skills.all()

@app.post("/memory/skills/{name}/use")
async def memory_skill_use(name: str):
    _init()
    from backend.dual_memory import get_closed_loop
    get_closed_loop().skills.use(name)
    return {"used": name}

@app.delete("/memory/skills/{name}")
async def memory_skill_archive(name: str):
    _init()
    from backend.dual_memory import get_closed_loop
    ok = get_closed_loop().skills.archive(name)
    if not ok: raise HTTPException(404, "Skill not found")
    return {"archived": name}

# === Honcho ===
@app.get("/memory/honcho")
async def memory_honcho_status():
    _init()
    from backend.dual_memory import get_closed_loop
    cl = get_closed_loop()
    return {
        "turns": cl.honcho._turns,
        "traits": cl.honcho.peer.traits,
        "preferences": cl.honcho.peer.preferences,
        "knowledge": cl.honcho.peer.knowledge_levels,
        "contradictions": cl.honcho.peer.contradictions,
        "context": cl.honcho.prompt_injection(),
    }

# === Nudge ===
@app.post("/memory/nudge/trigger")
async def memory_nudge_trigger():
    _init()
    from backend.dual_memory import get_closed_loop
    cl = get_closed_loop()
    p = cl.nudge.end_prompt(len(cl.agent_memory.entries))
    return {"nudge": p}

# === Curator ===
@app.post("/memory/curator/full")
async def memory_curator_full():
    _init()
    from backend.dual_memory import get_closed_loop
    from backend.agent.llm_client import MockLLMClient
    cl = get_closed_loop()
    # Use mock if no real LLM configured
    try:
        from backend.config import config
    except:
        config = None
    result = await cl.curator.light()  # Fallback to lightweight
    return {"curation": result}

# === FTS5 Sessions ===
@app.get("/memory/sessions/recent")
async def memory_sessions_recent(n: int = 10):
    _init()
    from backend.dual_memory import get_closed_loop
    return get_closed_loop().fts5.recent(n)

# === Plugin Marketplace ===
class MarketplaceInstallRequest(BaseModel):
    repo_url: str

@app.get("/marketplace")
async def marketplace_list():
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    plugins = mp.list_available()
    return {"count": len(plugins), "plugins": plugins}

@app.post("/marketplace/install")
async def marketplace_install(req: MarketplaceInstallRequest):
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    result = mp.install_from_github(req.repo_url)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Install failed"))
    return result

@app.delete("/marketplace/{name}")
async def marketplace_uninstall(name: str):
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    result = mp.uninstall(name)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Uninstall failed"))
    return result

@app.get("/marketplace/{name}/updates")
async def marketplace_check_updates(name: str):
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    return mp.check_updates(name)

@app.get("/marketplace/search")
async def marketplace_search(q: str = ""):
    from backend.plugin_marketplace import get_marketplace
    mp = get_marketplace()
    results = mp.search(q)
    return {"query": q, "count": len(results), "results": results}
