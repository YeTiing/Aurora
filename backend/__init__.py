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
from .magic_docs import MagicDocsManager, magic_docs_manager
from .im_adapter import AdapterBridge, adapter_bridge
from .session_search import SessionSearch, session_search
from .worktree import WorktreeManager, worktree_manager
from .context.collapse import ContextCollapser, context_collapser
from .skills import SkillManager, Skill, SkillResource, skill_manager
from .plugins import PluginManager, PluginInstance, PluginManifest, PluginInterface, plugin_manager
from .sandbox import DockerSandbox, SandboxConfig, sandbox
from .redis_cache import RedisClient, SessionCache, RAGCache, RateLimiter, EventBus, redis_client
from .prompt_templates import PromptTemplate, PromptManager, BUILTIN_TEMPLATES, prompt_manager
from .mcp_hub import MCPHub, MCPServerConfig as MCPSrvCfg, MCPServerState, mcp_hub
from .process_manager import ProcessManager, TrackedProcess, process_manager
from .sqlite_persistence import ThreadsDB, ThreadGoalsDB, AgentJobsDB, SpawnEdgesDB, LogsDB, MemoriesDB, StateDB, get_threads_db, get_thread_goals_db, get_agent_jobs_db, get_spawn_edges_db, get_logs_db, get_memories_db, get_state_db

from .lsp import LSPClient, LSPConfig, LSPServerManager, create_server_manager, get_manager as get_lsp_manager, get_builtin_configs
from .auto_dream import AutoDream, create_auto_dream
from .quality_gate import QualityGate, QualityGateConfig, QualityReport, TestRunner
from .provider_proxy import translate_tools, anthropic_to_openai_chat, openai_chat_to_anthropic, translate_stream_chunk, BillingInfo
from .swarm import InProcessBackend, TerminalBackend, BackendRegistry, get_backend_registry
from .computer_use.gates import CuGates, CuPermission, get_gates

from .bash_classifier import BashClassifier, BashRisk, classify_command, get_classifier
from .agent.integration_hooks import post_file_edit_hook, post_session_hook, pre_shell_exec_hook
from .tools.lsp_tool import LSP_TOOL_SPEC, lsp_handler
from .tools.verify_plan import VERIFY_TOOL_SPEC, verify_plan_handler, PlanVerifier, get_verifier
from .tools.tool_metrics import ToolMetrics, get_metrics
from .transcript_index import TranscriptIndex, get_transcript_index
from .hooks_system import HookRegistry, HookPoint, HookContext, HookResult, get_hook_registry
from .task_monitor import BackgroundMonitor, get_monitor
__version__ = "0.2.0"
