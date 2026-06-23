"""Aurora — AI 编程 Agent 引擎 v0.2.0 — Codex 架构启发"""

from .config import Config, init_config, config
from .agent import (
    AgentState, AgentStateDict, Message, PlanStep, ToolInvocation, ToolResult,
    LLMClient, LLMConfig, LLMError, RateLimitError, MockLLMClient,
    Checkpoint, CheckpointManager, AgentGraph, SYSTEM_PROMPT,
)
from .tools import (
    ToolSpec, ToolCallRequest, ToolCallResult, ToolRegistry, tool_registry,
    SHELL_SPEC, FILE_RW_SPEC, CODE_SEARCH_SPEC, APPLY_PATCH_SPEC, GIT_OPS_SPEC, WEB_FETCH_SPEC,
    MCPProxy, MCPServerConfig, mcp_proxy,
)
from .context import TokenCounter, TokenBudget, ContextManager, TokenTracker, tracker
from .rag import ASTChunker, BM25Index, VectorStore, Reranker, RAGEngine, rag_engine, init_rag
from .multi_agent import MultiAgentOrchestrator, AgentNode, AgentStatus, orchestrator
from .skills import SkillManager, Skill, SkillResource, skill_manager
from .plugins import PluginManager, PluginInstance, PluginManifest, PluginInterface, plugin_manager
from .sandbox import DockerSandbox, SandboxConfig, sandbox
from .redis_cache import RedisClient, SessionCache, RAGCache, RateLimiter, EventBus, redis_client
from .prompt_templates import PromptTemplate, PromptManager, BUILTIN_TEMPLATES, prompt_manager
from .mcp_hub import MCPHub, MCPServerConfig as MCPSrvCfg, MCPServerState, mcp_hub
from .process_manager import ProcessManager, TrackedProcess, process_manager
from .sqlite_persistence import ThreadsDB, ThreadGoalsDB, AgentJobsDB, SpawnEdgesDB, LogsDB, MemoriesDB, StateDB, get_threads_db, get_thread_goals_db, get_agent_jobs_db, get_spawn_edges_db, get_logs_db, get_memories_db, get_state_db

__version__ = "0.2.0"