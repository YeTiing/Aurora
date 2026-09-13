"""Aurora API - shared dependencies and init helpers."""
from __future__ import annotations
import collections
import threading
from fastapi import HTTPException
from backend.config import Config, init_config, config

_cfg: Config | None = None
_llm = None
_graph = None
_rag = None
_skills = None
_plugins = None
_init_lock = threading.RLock()

# 会话级 AgentGraph 注册表。
# 每个 AgentGraph 持有 per-session 可变状态（token_budget / _last_tracked_tokens /
# _cancelled_sessions），若全进程共用一个实例，并发会话会互相污染：
# 一个会话耗尽预算会掐断其他会话，token 增量记账也会串台。
# key 为 session_id；用 OrderedDict + 上限实现 LRU 淘汰，避免会话无限增长泄漏内存。
_DEFAULT_SESSION = "__default__"
_MAX_SESSION_GRAPHS = 32
_session_graphs: "collections.OrderedDict[str, object]" = collections.OrderedDict()
_session_graphs_lock = threading.RLock()


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = init_config(".")
    return _cfg


def get_llm():
    global _llm
    if _llm is None:
        with _init_lock:
            if _llm is None:
                _llm = _build_llm()
    return _llm


def _build_llm():
    """按当前配置构造一个 LLMClient（不写缓存）。

    会话级图需要各自的 client：AgentGraph.run 会按请求 model 调用 set_model()，
    若复用同一个 client，并发会话会互相覆盖 model。
    """
    from backend.agent.llm_client import LLMClient, LLMConfig
    cfg = get_config()
    # Try primary provider first
    providers = [
        {
            "provider": cfg.get("llm.provider", "openai"),
            "model": cfg.llm_model,
            "api_key": cfg.llm_api_key,
            "base_url": cfg.llm_base_url,
        },
    ]
    # Add fallback providers from config
    fallback_raw = cfg.get("llm.fallbacks", [])
    if fallback_raw:
        providers.extend(fallback_raw)
    last_err = None
    for p in providers:
        if not p.get("api_key"):
            last_err = HTTPException(503, detail="No API Key configured")
            continue
        try:
            return LLMClient(LLMConfig(
                provider=p.get("provider", "openai"),
                model=p.get("model", cfg.llm_model),
                api_key=p["api_key"],
                base_url=p.get("base_url", cfg.llm_base_url),
            ))
        except Exception as e:
            last_err = e
            import logging
            logging.getLogger("aurora").warning(f"LLM provider {p.get('provider')} unavailable: {e}")
    if last_err is not None:
        if isinstance(last_err, HTTPException):
            raise last_err
        raise HTTPException(503, detail=f"All LLM providers unavailable: {last_err}")


def _make_graph(llm) -> "AgentGraph":
    from backend.agent.graph import AgentGraph
    from backend.agent.checkpoint import get_checkpoint_manager
    from backend.tools import tool_registry
    cfg = get_config()
    async def tool_handler(name, args, ws):
        result = await tool_registry.execute(name, args, ws)
        return {"success": result.success, "output": result.output, "error": result.error}
    return AgentGraph(
        llm=llm, tool_handler=tool_handler,
        tools_schema=tool_registry.list_tools_openai(),
        max_turns=cfg.max_turn_iter, workspace=".",
        # 与 checkpoint 路由共用同一管理器，undo/redo 才能看到执行期落的快照
        checkpoint_manager=get_checkpoint_manager(),
    )


def get_graph_for(session_id: str = ""):
    """返回某会话专属的 AgentGraph。

    每个会话独立持有 token_budget / _last_tracked_tokens / model / LLM client，
    并发会话互不影响。超过上限时按 LRU 淘汰最久未用的会话图。
    """
    key = session_id or _DEFAULT_SESSION
    with _session_graphs_lock:
        g = _session_graphs.get(key)
        if g is not None:
            _session_graphs.move_to_end(key)
            return g
        # 外部（测试/嵌入方）直接替换 _graph 时视为全局覆盖：
        # 正常流程里 _graph 就是默认会话的图（同一对象），不会命中此分支，
        # 因此生产环境仍是每会话一图，不会退回共享单例。
        if _graph is not None and _graph is not _session_graphs.get(_DEFAULT_SESSION):
            return _graph
        g = _make_graph(_build_llm())
        _session_graphs[key] = g
        while len(_session_graphs) > _MAX_SESSION_GRAPHS:
            _session_graphs.popitem(last=False)
        return g


def drop_graph_for(session_id: str) -> None:
    """会话取消/结束后显式释放，避免注册表长期保留已死会话。"""
    with _session_graphs_lock:
        _session_graphs.pop(session_id or _DEFAULT_SESSION, None)


def get_graph():
    global _graph
    if _graph is None:
        with _init_lock:
            if _graph is None:
                # 兼容旧调用方（测试与部分路由直接调 graph()）：
                # 委托给默认会话的会话级图，而不再是所有会话共享的可变单例。
                _graph = get_graph_for(_DEFAULT_SESSION)
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


# Convenience accessors - call these instead of per-file lazy init globals
def cfg(): return get_config()
def llm(): return get_llm()
def graph(): return get_graph()
def graph_for(session_id: str = ""): return get_graph_for(session_id)
def rag(): return get_rag()
def skills(): return get_skills()
def plugins(): return get_plugins()

def ensure_all():
    """Lazy-init all cached dependencies. Idempotent - safe to call repeatedly."""
    get_config(); get_llm(); get_graph(); get_rag(); get_skills(); get_plugins()

def reset_deps():
    """Reset all cached dependencies (for testing)."""
    global _cfg, _llm, _graph, _rag, _skills, _plugins
    _cfg = None; _llm = None; _graph = None; _rag = None; _skills = None; _plugins = None
    with _session_graphs_lock:
        _session_graphs.clear()
