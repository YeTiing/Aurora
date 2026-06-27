"""Aurora API - system routes"""
from __future__ import annotations
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

class ApprovalAction(BaseModel):
    request_id: str
    action: str  # approve / deny

class CreateThreadRequest(BaseModel):
    title: str = ""; workspace: str = ""; model: str = ""; reasoning_effort: str = "medium"; thread_source: str = "user"

class DiffMergeRequest(BaseModel):
    base: str; ours: str; theirs: str

@router.post("/plugins/{name}/load")
async def load_plugin(name: str): _init_plugins(); return {"name":name,"loaded":_plugins.load(name)}

@router.post("/plugins/{name}/unload")
async def unload_plugin(name: str): _init_plugins(); return {"name":name,"unloaded":_plugins.unload(name)}

@router.post("/mcp/servers/start")
async def start_mcp_server(req: dict):
    from backend.mcp_hub import mcp_hub, MCPServerConfig
    cfg = MCPServerConfig(**{k: req.get(k,v) for k,v in MCPServerConfig.__dataclass_fields__.items() if k in req or hasattr(MCPServerConfig,k)})
    return {"name":cfg.name,"started":await mcp_hub.start_server(cfg)}

@router.post("/mcp/servers/{name}/stop")
async def stop_mcp_server(name: str):
    from backend.mcp_hub import mcp_hub; await mcp_hub.stop_server(name); return {"name":name,"stopped":True}

# Processes
@router.get("/processes")
async def list_processes():
    from backend.process_manager import process_manager
    procs = process_manager.list_all()
    return {"processes":[{"id":p.id,"command":p.command,"status":p.status} for p in procs],"stats":process_manager.stats()}

@router.get("/processes/{proc_id}")
async def get_process(proc_id: str):
    """Get a specific tracked process by ID."""
    from backend.process_manager import process_manager
    proc = process_manager.get(proc_id)
    if not proc:
        raise HTTPException(404, f"Process not found: {proc_id}")
    return {
        "id": proc.id,
        "command": proc.command,
        "os_pid": proc.os_pid,
        "conversation_id": proc.conversation_id,
        "turn_id": proc.turn_id,
        "cwd": proc.cwd,
        "status": proc.status,
        "started_at_ms": proc.started_at_ms,
        "finished_at_ms": proc.finished_at_ms,
        "exit_code": proc.exit_code,
    }


@router.post("/processes/{proc_id}/kill")
async def kill_process(proc_id: str):
    from backend.process_manager import process_manager
    return {"id":proc_id,"killed":process_manager.kill(proc_id)}

# Storage
@router.get("/storage")
async def storage_overview():
    """Return disk usage info for the .aurora directory."""
    import shutil
    aurora_dir = Path.home() / ".aurora"
    total_size = 0
    file_count = 0
    if aurora_dir.exists():
        for f in aurora_dir.rglob("*"):
            if f.is_file():
                try:
                    total_size += f.stat().st_size
                    file_count += 1
                except OSError:
                    pass
    disk_usage = shutil.disk_usage(str(aurora_dir)) if aurora_dir.exists() else None
    return {
        "aurora_dir": str(aurora_dir),
        "file_count": file_count,
        "total_size_bytes": total_size,
        "total_size_human": _fmt_bytes(total_size),
        "disk_free_bytes": disk_usage.free if disk_usage else 0,
        "disk_total_bytes": disk_usage.total if disk_usage else 0,
    }


@router.get("/storage/stats")
async def storage_stats():
    from backend.sqlite_persistence import get_thread_goals_db, get_logs_db, get_memories_db
    return {"goals":get_thread_goals_db().stats(),"memories":len(get_memories_db().list_keys())}

# ========== Log Archive Management ==========

@router.get("/logs/stats")
async def logs_stats():
    """Get log statistics: total, by level, by module, date range."""
    from backend.log_archive import LogArchiveManager
    mgr = LogArchiveManager()
    return mgr.get_log_stats()


@router.post("/logs/archive")
async def logs_archive(req: dict):
    """Archive logs older than N days. Body: {"before_days": 30}"""
    from backend.log_archive import LogArchiveManager
    mgr = LogArchiveManager()
    before_days = req.get("before_days", 30)
    return mgr.archive_old_logs(before_days=int(before_days))


@router.post("/logs/cleanup")
async def logs_cleanup(req: dict):
    """Delete logs older than N days. Body: {"before_days": 90}"""
    from backend.log_archive import LogArchiveManager
    mgr = LogArchiveManager()
    before_days = req.get("before_days", 90)
    return mgr.cleanup_old_logs(before_days=int(before_days))


@router.get("/logs/archives")
async def logs_archives():
    """List archive files with size, date range, and log count."""
    from backend.log_archive import LogArchiveManager
    mgr = LogArchiveManager()
    return {"archives": mgr.get_archive_list()}


@router.post("/logs/restore/{name}")
async def logs_restore(name: str):
    """Restore logs from an archive file back to the database."""
    from backend.log_archive import LogArchiveManager
    mgr = LogArchiveManager()
    try:
        restored = mgr.restore_archive(name)
        return {"restored": restored, "archive": name}
    except FileNotFoundError:
        raise HTTPException(404, f"Archive not found: {name}")


@router.get("/storage/memories")
async def list_memories(category: str|None=None):
    from backend.sqlite_persistence import get_memories_db
    db = get_memories_db(); keys = db.list_keys(category)
    return {"count":len(keys),"memories":{k:db.get(k) for k in keys[:50]}}

# ═══════════ Settings / Models / LLM Test ═══════════

@router.post("/browser/navigate")
async def browser_navigate(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.navigate(req.get("url", "https://google.com"))
    return {"ok": result.get("error") is None, "result": result}

@router.post("/browser/screenshot")
async def browser_screenshot():
    from backend.browser_use import browser_use
    result = await browser_use.screenshot()
    return {"ok": result.get("error") is None, **result}

@router.post("/browser/click")
async def browser_click(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.click(req.get("selector", "body"))
    return {"ok": result.get("error") is None, "result": result}

@router.post("/browser/type")
async def browser_type(req: dict):
    from backend.browser_use import browser_use
    result = await browser_use.type_text(req.get("selector", "input"), req.get("text", ""))
    return {"ok": True, "result": result}

# ═══════════ Approval ═══════════
@router.get("/approval/status")
async def approval_status():
    from backend.approval import approval_manager
    return approval_manager.stats()

@router.get("/approval/pending")
async def approval_pending():
    from backend.approval import approval_bridge
    pending = approval_bridge.manager.get_pending()
    return {"count": len(pending), "requests": [{"id": r.id, "type": r.type, "risk": r.risk_level.value, "description": r.description} for r in pending]}

@router.post("/approval/{action}")
async def approval_act(action: str, req: ApprovalAction):
    from backend.approval import approval_bridge
    if action not in ("approve", "deny"):
        raise HTTPException(400, "action must be approve or deny")
    return await approval_bridge.decide(req.request_id, action, session_id="system", thread_id="system")

# ═══════════ Threads ═══════════
@router.get("/threads")
async def list_threads(status: str = ""):
    from backend.thread_manager import thread_manager
    threads = thread_manager.list_threads(status)
    return {"count": len(threads), "threads": [{"id": t.id, "title": t.title, "status": t.status, "model": t.model, "reasoning_effort": t.reasoning_effort} for t in threads]}

@router.post("/threads/{thread_id}/fork")
async def fork_thread(thread_id: str, req: dict = {}):
    from backend.thread_manager import thread_manager
    t = thread_manager.fork(thread_id, req.get("title", ""))
    if not t: raise HTTPException(404, "Thread not found")
    return {"id": t.id, "title": t.title, "forked_from": thread_id}

@router.get("/threads/{thread_id}")
async def read_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    t = thread_manager.read(thread_id)
    if not t: raise HTTPException(404, "Not found")
    return {"id": t.id, "title": t.title, "status": t.status, "workspace": t.workspace, "model": t.model, "reasoning_effort": t.reasoning_effort, "parent_id": t.parent_id}

@router.post("/threads/{thread_id}/pin")
async def pin_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_pinned(thread_id)
    return {"id": thread_id, "pinned": ok}

@router.post("/threads/{thread_id}/archive")
async def archive_thread(thread_id: str):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_archived(thread_id)
    return {"id": thread_id, "archived": ok}

@router.post("/threads/{thread_id}/title")
async def rename_thread(thread_id: str, req: dict):
    from backend.thread_manager import thread_manager
    ok = thread_manager.set_title(thread_id, req.get("title", ""))
    return {"id": thread_id, "renamed": ok}

# ═══════════ Heartbeat ═══════════
@router.get("/heartbeat/status")
async def heartbeat_status():
    from backend.heartbeat import heartbeat_manager
    return {"enabled": heartbeat_manager._enabled, "interval": heartbeat_manager._config.interval_seconds}

@router.post("/heartbeat/send")
async def heartbeat_send(req: dict):
    from backend.heartbeat import heartbeat_manager
    msg = heartbeat_manager.notify(req.get("message", "Checking in"))
    return {"heartbeat": msg[:200]}

# ═══════════ Sentry ═══════════
@router.post("/sentry/test")
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
@router.get("/agents-md")
async def list_agents_md():
    from backend.agents_md import agents_loader
    rules = agents_loader.scan(".")
    return {"count": len(rules), "files": [str(r.file_path) for r in rules]}

@router.get("/agents-md/read")
async def read_agents_md(path: str = "."):
    from backend.agents_md import agents_loader
    content = agents_loader.inject(path)
    return {"path": path, "has_instructions": bool(content), "content": content[:3000]}


# ── File Upload ──
@router.post("/files/upload")
async def upload_file(file: UploadFile = File(...), workspace: str = Form(".")):
    """Upload a file to the workspace uploads directory."""
    if not file.filename:
        raise HTTPException(400, "No file provided")
    safe_name = Path(file.filename).name  # prevent path traversal
    if not safe_name or safe_name != file.filename.replace("\\", "/").split("/")[-1]:
        raise HTTPException(400, "Invalid filename")
    ws_dir = Path(workspace) / "uploads"
    ws_dir.mkdir(parents=True, exist_ok=True)
    file_path = ws_dir / safe_name
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
async def auth_logout():
    from backend.auth import auth_manager
    return auth_manager.logout()

# ========== Tool Call Blocks Debug ==========
@router.get("/tools/blocks")
async def tools_blocks():
    from backend.agent.tool_call_aggregator import tool_call_aggregator
    return tool_call_aggregator.get_snapshot()

@router.post("/tools/blocks/reset")
async def tools_blocks_reset():
    from backend.agent.tool_call_aggregator import tool_call_aggregator
    tool_call_aggregator.reset()
    return {"reset": True, "snapshot": tool_call_aggregator.get_snapshot()}

# ========== Model Discovery ==========
@router.post("/models/discover")
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



# ─── Notifications ───

@router.get("/notifications")
async def list_notifications(limit: int = 50):
    """List recent notifications."""
    from backend.notifications import get_notification_manager
    nm = get_notification_manager()
    return {"count": nm.count(), "notifications": nm.list_recent(limit)}

@router.post("/notifications/dismiss/{notification_id}")
async def dismiss_notification(notification_id: str):
    """Dismiss a notification by ID."""
    from backend.notifications import get_notification_manager
    nm = get_notification_manager()
    ok = nm.dismiss(notification_id)
    if not ok:
        raise HTTPException(404, f"Notification not found: {notification_id}")
    return {"id": notification_id, "dismissed": True}

@router.post("/notifications/send")
async def send_notification(req: dict):
    """Send a test notification: {title, body, urgency}."""
    from backend.notifications import get_notification_manager
    nm = get_notification_manager()
    notif = nm.send(
        title=req.get("title", "Aurora"),
        body=req.get("body", ""),
        urgency=req.get("urgency", "normal"),
    )
    return {"sent": True, "notification": notif.to_dict()}



# ========== Deep Link Router ==========

class DeepLinkParseRequest(BaseModel):
    url: str

@router.post("/deep-link/parse")
async def deep_link_parse(req: DeepLinkParseRequest):
    """Parse an aurora:// URL into its components."""
    from backend.deep_link import deep_link_router
    result = deep_link_router.parse(req.url)
    return {
        "valid": deep_link_router.validate(req.url),
        "scheme": result.scheme,
        "host": result.host,
        "path": result.path,
        "params": result.params,
    }

@router.post("/deep-link/route")
async def deep_link_route(req: DeepLinkParseRequest):
    """Route an aurora:// URL to the appropriate action."""
    from backend.deep_link import deep_link_router
    if not deep_link_router.validate(req.url):
        raise HTTPException(400, f"Invalid deep link: {req.url}")
    action = deep_link_router.route(req.url)
    return action.to_dict()



# ---- Log Archive routes defined above ----



def _fmt_bytes(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ---- ELF Parser ----

@router.post("/tools/elf/parse")
async def elf_parse(req: dict):
    """Parse an ELF binary file and return header/section/program info."""
    path = req.get("path", "")
    if not path:
        raise HTTPException(400, "Missing 'path' in request body")
    from backend.elf_parser import ELFParser
    import os as _os
    if not _os.path.isfile(path):
        raise HTTPException(404, f"File not found: {path}")
    try:
        info = ELFParser.parse(path)
        return {"success": True, "info": info.to_dict()}
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.post("/tools/elf/check")
async def elf_check(req: dict):
    """Check if a file is an ELF binary."""
    path = req.get("path", "")
    if not path:
        raise HTTPException(400, "Missing 'path' in request body")
    from backend.elf_parser import ELFParser
    return {"path": path, "is_elf": ELFParser.is_elf(path)}

# ---- Package Manager ----

@router.get("/packages/detect")
async def pkg_detect(project_dir: str = "."):
    """Detect which package ecosystems are present in a project."""
    from backend.package_manager import PackageManager
    ecosystems = PackageManager.detect_ecosystem(project_dir)
    return {"project_dir": project_dir, "ecosystems": [e.value for e in ecosystems]}

@router.post("/packages/install")
async def pkg_install(req: dict):
    """Install dependencies for a project."""
    from backend.package_manager import PackageManager, PackageEcosystem
    project_dir = req.get("project_dir", ".")
    ecosystem_str = req.get("ecosystem")
    ecosystem = PackageEcosystem(ecosystem_str) if ecosystem_str else None
    result = await PackageManager.install(project_dir, ecosystem)
    return result.to_dict()

@router.post("/packages/list")
async def pkg_list(req: dict):
    """List installed packages in a project."""
    from backend.package_manager import PackageManager, PackageEcosystem
    project_dir = req.get("project_dir", ".")
    ecosystem_str = req.get("ecosystem")
    ecosystem = PackageEcosystem(ecosystem_str) if ecosystem_str else None
    result = await PackageManager.list_packages(project_dir, ecosystem)
    return result.to_dict()

@router.post("/packages/outdated")
async def pkg_outdated(req: dict):
    """Check for outdated packages."""
    from backend.package_manager import PackageManager, PackageEcosystem
    project_dir = req.get("project_dir", ".")
    ecosystem_str = req.get("ecosystem")
    ecosystem = PackageEcosystem(ecosystem_str) if ecosystem_str else None
    result = await PackageManager.check_outdated(project_dir, ecosystem)
    return result.to_dict()

@router.post("/packages/add")
async def pkg_add(req: dict):
    """Add a package to a project."""
    from backend.package_manager import PackageManager, PackageEcosystem
    name = req.get("name", "")
    if not name:
        raise HTTPException(400, "Missing 'name' in request body")
    project_dir = req.get("project_dir", ".")
    ecosystem_str = req.get("ecosystem")
    ecosystem = PackageEcosystem(ecosystem_str) if ecosystem_str else None
    dev = req.get("dev", False)
    result = await PackageManager.add_package(name, project_dir, ecosystem, dev)
    return result.to_dict()
