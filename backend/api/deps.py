"""Aurora API - shared dependencies and init helpers."""
from __future__ import annotations
from fastapi import HTTPException
from backend.config import Config, init_config, config

_cfg: Config | None = None
_llm = None
_graph = None
_rag = None
_skills = None
_plugins = None


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = init_config(".")
    return _cfg


def get_llm():
    global _llm
    if _llm is None:
        from backend.agent.llm_client import LLMClient, LLMConfig
        cfg = get_config()
        if cfg.llm_api_key:
            _llm = LLMClient(LLMConfig(
                model=cfg.llm_model,
                api_key=cfg.llm_api_key,
                base_url=cfg.llm_base_url
            ))
        else:
            raise HTTPException(503, detail="No API Key configured")
    return _llm


def get_graph():
    global _graph
    if _graph is None:
        from backend.agent.graph import AgentGraph
        from backend.tools import tool_registry
        llm = get_llm()
        cfg = get_config()
        async def tool_handler(name, args, ws):
            result = await tool_registry.execute(name, args, ws)
            return {"success": result.success, "output": result.output, "error": result.error}
        _graph = AgentGraph(
            llm=llm, tool_handler=tool_handler,
            tools_schema=tool_registry.list_tools_openai(),
            max_turns=cfg.max_turn_iter, workspace="."
        )
    return _graph


def get_rag():
    global _rag
    if _rag is None:
        from backend.rag import rag_engine
        _rag = rag_engine
    return _rag


def get_skills():
    global _skills
    if _skills is None:
        from backend.skills import skill_manager
        _skills = skill_manager
    return _skills


def get_plugins():
    global _plugins
    if _plugins is None:
        from backend.plugins import plugin_manager
        _plugins = plugin_manager
    return _plugins


# WebSocket connection registry (shared across routes)
_ws_connections: dict[str, object] = {}


def reset_deps():
    """Reset all cached dependencies (for testing)."""
    global _cfg, _llm, _graph, _rag, _skills, _plugins
    _cfg = None; _llm = None; _graph = None; _rag = None; _skills = None; _plugins = None
