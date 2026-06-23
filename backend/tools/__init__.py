# 工具系统 __init__ — 导出 + 自动注册
from .base import ToolSpec, ToolCallRequest, ToolCallResult, ToolRegistry, tool_registry, safe_resolve_path, sanitize_command, truncate_output
from .shell_command import SHELL_SPEC, shell_handler
from .file_rw import FILE_RW_SPEC, file_rw_handler
from .code_search import CODE_SEARCH_SPEC, code_search_handler
from .apply_patch import APPLY_PATCH_SPEC, apply_patch_handler
from .git_ops import GIT_OPS_SPEC, git_ops_handler, git_ops_handler_safe
from .web_fetch import WEB_FETCH_SPEC, web_fetch_handler
from .mcp_proxy import MCPProxy, MCPServerConfig, MCPServerState, mcp_proxy
from .todo_write import TODO_SPEC, todo_handler, PLAN_UPDATE_SPEC, plan_update_handler, get_current_todos
from .code_exec import CODE_EXEC_SPEC, code_exec_handler
from .view_image import VIEW_IMAGE_SPEC, view_image_handler
from .web_search import WEB_SEARCH_SPEC, web_search_handler
from .request_user_input import REQUEST_USER_INPUT_SPEC, request_user_input_handler, get_pending_requests, resolve_request
from .list_files import LIST_FILES_SPEC, list_files_handler
from .send_message import SEND_MESSAGE_SPEC, send_message_handler
from .computer_use import COMPUTER_USE_SPEC, computer_use_handler
from .browser_use import BROWSER_USE_SPEC, browser_use_handler
from .memory import MEMORY_SPEC, memory_handler
from .cron_tool import CRON_SPEC, cron_handler
from .skin_tool import SKIN_SPEC, skin_handler
from .re_tool import RE_SPEC, re_handler
from .detective_tool import DETECTIVE_SPEC, detective_handler

def register_all_tools():
    tool_registry.register(SHELL_SPEC, shell_handler)
    tool_registry.register(FILE_RW_SPEC, file_rw_handler)
    tool_registry.register(CODE_SEARCH_SPEC, code_search_handler)
    tool_registry.register(APPLY_PATCH_SPEC, apply_patch_handler)
    tool_registry.register(GIT_OPS_SPEC, git_ops_handler_safe)
    tool_registry.register(WEB_FETCH_SPEC, web_fetch_handler)
    tool_registry.register(TODO_SPEC, todo_handler)
    tool_registry.register(PLAN_UPDATE_SPEC, plan_update_handler)
    tool_registry.register(CODE_EXEC_SPEC, code_exec_handler)
    tool_registry.register(VIEW_IMAGE_SPEC, view_image_handler)
    tool_registry.register(WEB_SEARCH_SPEC, web_search_handler)
    tool_registry.register(REQUEST_USER_INPUT_SPEC, request_user_input_handler)
    tool_registry.register(LIST_FILES_SPEC, list_files_handler)
    tool_registry.register(SEND_MESSAGE_SPEC, send_message_handler)
    tool_registry.register(COMPUTER_USE_SPEC, computer_use_handler)
    tool_registry.register(BROWSER_USE_SPEC, browser_use_handler)
tool_registry.register(MEMORY_SPEC, memory_handler)
tool_registry.register(CRON_SPEC, cron_handler)
tool_registry.register(SKIN_SPEC, skin_handler)
tool_registry.register(RE_SPEC, re_handler)
tool_registry.register(DETECTIVE_SPEC, detective_handler)

register_all_tools()

__all__ = [
    "ToolSpec", "ToolCallRequest", "ToolCallResult", "ToolRegistry", "tool_registry",
    "safe_resolve_path", "sanitize_command", "truncate_output",
    "SHELL_SPEC", "shell_handler",
    "FILE_RW_SPEC", "file_rw_handler",
    "CODE_SEARCH_SPEC", "code_search_handler",
    "APPLY_PATCH_SPEC", "apply_patch_handler",
    "GIT_OPS_SPEC", "git_ops_handler", "git_ops_handler_safe",
    "WEB_FETCH_SPEC", "web_fetch_handler",
    "TODO_SPEC", "todo_handler", "PLAN_UPDATE_SPEC", "plan_update_handler", "get_current_todos",
    "CODE_EXEC_SPEC", "code_exec_handler",
    "VIEW_IMAGE_SPEC", "view_image_handler",
    "WEB_SEARCH_SPEC", "web_search_handler",
    "REQUEST_USER_INPUT_SPEC", "request_user_input_handler",
    "LIST_FILES_SPEC", "list_files_handler",
    "SEND_MESSAGE_SPEC", "send_message_handler",
    "COMPUTER_USE_SPEC", "computer_use_handler",
    "BROWSER_USE_SPEC", "browser_use_handler",
    "get_pending_requests", "resolve_request",
    "MCPProxy", "MCPServerConfig", "MCPServerState", "mcp_proxy",
    "register_all_tools",
        "MEMORY_SPEC", "memory_handler",
        "CRON_SPEC", "cron_handler",
    "SKIN_SPEC", "skin_handler",
    "RE_SPEC", "re_handler",
    "DETECTIVE_SPEC", "detective_handler",
]