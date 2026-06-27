"""Aurora API - settings routes"""
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

from backend.api.models import SentryConfig, RenderPromptRequest, ConfigUpdateRequest, SettingsUpdate, LLMTestRequest

from backend.config import config as _cfg_module
from backend.api.routes.detective import ProviderProfile
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

def _init():
    _init_cfg()

def _init_plugins():
    global _plugins
    _plugins = _get_plugins()





@router.post("/config")
async def update_config(req: ConfigUpdateRequest):
    _init()
    if req.value is not None:
        _cfg.set(req.key, req.value)
        return {"key": req.key, "value": req.value, "updated": True}
    return {"key": req.key, "current": _cfg.get(req.key)}

# Plugins
@router.get("/plugins")
async def list_plugins(): _init_plugins(); return {"plugins":_plugins.list_all()}

@router.post("/plugins/{name}/reload")
async def reload_plugin(name: str): _init_plugins(); return {"name":name,"reloaded":_plugins.reload(name)}

# Prompts
@router.get("/prompts")
async def list_prompts(category: str|None=None, search: str|None=None):
    from backend.prompt_templates import prompt_manager
    if search: tpls = prompt_manager.search(search)
    elif category: tpls = prompt_manager.by_category(category)
    else: tpls = list(prompt_manager.templates.values())
    return {"count":len(tpls),"templates":[{"name":t.name,"description":t.description,"category":t.category} for t in tpls]}

@router.post("/prompts/render")
async def render_prompt(req: RenderPromptRequest):
    from backend.prompt_templates import prompt_manager
    result = prompt_manager.render(req.name, **req.variables)
    if result.startswith("Template"): raise HTTPException(404, result)
    return {"rendered":result}

# Multi-Agent
@router.get("/agents/tree")
async def agent_tree():
    from backend.multi_agent import orchestrator; return orchestrator.get_tree()

@router.get("/settings")
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
        "vision_fallback": _cfg.get("vision_fallback", {"enabled": True, "model": "gpt-4o-mini", "provider": "openai"}),
    }

@router.post("/settings")
async def update_settings(req: SettingsUpdate):
    _init()
    config_path = Path.home() / ".aurora" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if config_path.exists():
        try: existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            import logging; logging.getLogger("aurora").warning(f"Failed to read config, backing up: {e}")
            try: config_path.rename(config_path.with_suffix(".toml.bak"))
            except Exception: logger.debug('config get failed', exc_info=True)
            existing = {}
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
    if req.vision_fallback is not None: existing["vision_fallback"] = req.vision_fallback
    config_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    # Also update project aurora.json so it takes precedence over config.toml
    aurora_json = Path("aurora.json")
    aurora_data = {}
    if aurora_json.exists():
        try: aurora_data = json.loads(aurora_json.read_text(encoding="utf-8"))
        except Exception: logger.debug('settings update failed', exc_info=True)
    if llm_updates: aurora_data.setdefault("llm", {}).update(llm_updates)
    if req.max_context_tokens: aurora_data.setdefault("context", {}).update({"token_budget": req.max_context_tokens, "max_tokens": req.max_context_tokens})
    if req.max_turn_iter: aurora_data.setdefault("agent", {}).update({"max_turn_iter": req.max_turn_iter})
    if req.vision_fallback is not None: aurora_data["vision_fallback"] = req.vision_fallback
    if req.model: aurora_data["model"] = req.model
    if req.provider: aurora_data["provider"] = req.provider
    aurora_json.write_text(json.dumps(aurora_data, indent=4, ensure_ascii=False), encoding="utf-8")
    import backend.api.deps as deps
    import backend.api.routes.chat as chat_routes
    deps.reset_deps()
    chat_routes._cfg = None; chat_routes._llm = None; chat_routes._graph = None
    chat_routes._rag = None; chat_routes._skills = None; chat_routes._plugins = None
    global _cfg, _llm, _graph
    _cfg = None; _llm = None; _graph = None
    _init()
    try: _init_llm()
    except Exception: logger.debug('llm_test failed', exc_info=True)
    try: _init_graph()
    except Exception: logger.debug('llm_test cleanup failed', exc_info=True)
    return {"ok": True, "updated": list(llm_updates.keys())}

@router.get("/models")
async def list_models():
    _init()
    from backend.model_discovery import model_discovery as md
    base_url = _cfg.get("llm.base_url", "https://api.openai.com/v1")
    api_key = _cfg.get("llm.api_key", "")
    provider = _cfg.get("llm.provider", "openai")
    models = await md.list_models(base_url, api_key, provider)
    return {"count":len(models),"models":[{"id":m.id,"max_tokens":m.max_tokens,"provider":m.provider} for m in models],"base_url":base_url}

@router.get("/models/context")
async def model_context_info(model: str = ""):
    _init()
    from backend.model_discovery import model_discovery as md
    m = model or _cfg.get("llm.model", "gpt-4o")
    return md.get_context_limit(m, _cfg.get("context.max_tokens", 0))

@router.post("/llm/test")
async def test_llm(req: LLMTestRequest = LLMTestRequest()):
    _init_llm()
    try:
        resp = await _llm.chat([{"role":"user","content":req.message}], max_tokens=50, temperature=0.1)
        return {"ok":True,"response":resp.content[:200],"model":resp.model or "","tokens":resp.total_tokens or 0}
    except Exception as e:
        return {"ok":False,"error":str(e)[:300]}


# ═══════════ Browser Use ═══════════
@router.get("/browser/pages")
async def browser_list_pages():
    from backend.browser_use import browser_use
    pages = await browser_use.list_pages()
    return {"pages": [{"url": p.url, "title": p.title, "id": p.target_id} for p in pages]}

@router.post("/sentry/configure")
async def configure_sentry(req: SentryConfig):
    try:
        import sentry_sdk
        if req.enabled and req.dsn:
            sentry_sdk.init(dsn=req.dsn, traces_sample_rate=1.0)
            return {"ok": True, "message": "Sentry configured"}
        return {"ok": False, "message": "DSN required"}
    except ImportError:
        return {"ok": False, "message": "sentry-sdk not installed. pip install sentry-sdk"}

@router.get("/presets/{category}")
async def list_presets_by_category(category: str):
    """List prompt presets by category."""
    from backend.prompt_presets import preset_manager as pm
    presets = pm.list_by_category(category)
    return {"category": category, "count": len(presets), "presets": presets}

@router.post("/presets/render")
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

@router.post("/models/discover/all")
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

@router.get("/models/benchmarks")
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

@router.post("/models/test")
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

@router.get("/models/recommend")
async def models_recommend(type: str = "code"):
    from backend.model_discovery import model_discovery
    return model_discovery.recommend(type)

@router.post("/models/cache/purge")
async def models_cache_purge():
    from backend.model_discovery import model_discovery
    removed = model_discovery.cache_results(ttl_seconds=0)
    return {"removed": removed, "remaining_cache": model_discovery.get_cache_age()}


# === Cron Scheduler ===
@router.get("/cron")
async def cron_list():
    """List all cron tasks."""
    from backend.cron_scheduler import get_cron
    return get_cron().list_tasks()

@router.get("/providers")
async def list_providers():
    """List only user-saved provider profiles."""
    _init()
    config_path = Path.home() / ".aurora" / "config.toml"
    saved = []
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            saved = data.get("providers", [])
        except Exception: logger.debug('preset load failed', exc_info=True)
    result = []
    for s in saved:
        result.append({
            "name": s.get("name", "Custom"),
            "provider": s.get("provider", "openai"),
            "api_key": s.get("api_key", ""),
            "base_url": s.get("base_url", ""),
            "model": s.get("model", "gpt-4o"),
            "max_context_tokens": s.get("max_context_tokens", 24000),
        })
    return {"providers": result}

@router.post("/providers")
async def save_provider(req: ProviderProfile):
    """Save a provider profile."""
    _init()
    config_path = Path.home() / ".aurora" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if config_path.exists():
        try: existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception: logger.debug('prompt load failed', exc_info=True)
    
    providers = existing.get("providers", [])
    # Find and update or append
    found = False
    for p in providers:
        if p.get("name") == req.name:
            p.update(req.model_dump(exclude_none=True))
            found = True
            break
    if not found:
        providers.append(req.model_dump(exclude_none=True))
    
    existing["providers"] = providers
    config_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "name": req.name}

@router.delete("/providers/{name}")
async def delete_provider(name: str):
    """Delete a saved provider profile."""
    _init()
    config_path = Path.home() / ".aurora" / "config.toml"
    if not config_path.exists():
        return {"ok": False, "error": "No saved providers"}
    try:
        existing = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "Config read error"}
    
    providers = existing.get("providers", [])
    existing["providers"] = [p for p in providers if p.get("name") != name]
    config_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # If the currently active provider matches, don't wipe it - just remove from list
    return {"ok": True, "deleted": name}

# ═══════════ Session Export ═══════════

