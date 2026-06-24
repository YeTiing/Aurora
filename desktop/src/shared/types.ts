// Aurora shared types — complete Codex SSE event system

export interface AgentMessage {
    role: "user" | "assistant" | "system" | "tool";
    content: string;
    timestamp: number;
    id: string;
}

export interface ToolCall {
    id: string;
    name: string;
    arguments: Record<string, any>;
}

export interface ToolResult {
    toolName: string;
    output: string;
    success: boolean;
    error?: string;
}

export interface ToolLog {
    type: "tool_start" | "tool_end" | "tool_output";
    tool: string;
    toolCallId: string;
    args?: Record<string, any>;
    success?: boolean;
    output?: string;
    error?: string;
    timestamp: number;
}

export interface PlanStep {
    step: number;
    description: string;
    status: "pending" | "in_progress" | "completed" | "failed";
}

export interface Session {
    id: string;
    title: string;
    workspace: string;
    messages: AgentMessage[];
    plan: PlanStep[];
    toolLogs: ToolLog[];
    createdAt: number;
    updatedAt: number;
    pinned: boolean;
    archived: boolean;
}

// Codex SSE Event type
export interface SSEEvent {
    id?: string;
    type: string;
    data?: Record<string, any>;
    session_id?: string;
    thread_id?: string;
    timestamp?: number;
}

// Codex SSE Event types — synced with backend sse_events.py
export type SSEEventType =
    // Thread Follower Controls
    | "codex/event/thread_follower_start_turn"
    | "codex/event/thread_follower_steer_turn"
    | "codex/event/thread_follower_interrupt_turn"
    | "codex/event/thread_follower_edit_last_user_turn"
    | "codex/event/thread_follower_compact_thread"
    | "codex/event/thread_follower_load_complete_history"
    | "codex/event/thread_follower_command_approval_decision"
    | "codex/event/thread_follower_file_approval_decision"
    | "codex/event/thread_follower_permissions_request_approval_response"
    | "codex/event/thread_follower_submit_user_input"
    | "codex/event/thread_follower_submit_mcp_server_elicitation_response"
    | "codex/event/thread_follower_set_queued_followups_state"
    | "codex/event/thread_follower_update_thread_settings"
    // Session / Task Lifecycle
    | "codex/event/session_configured"
    | "codex/event/task_started"
    | "codex/event/task_complete"
    | "codex/event/turn_aborted"
    | "codex/event/turn_diff"
    | "codex/event/undo_started"
    | "codex/event/undo_completed"
    | "codex/event/error"
    | "codex/event/stream_error"
    | "codex/event/warning"
    | "codex/event/shutdown_complete"
    // Agent Reasoning / Messages
    | "codex/event/agent_reasoning"
    | "codex/event/agent_reasoning_delta"
    | "codex/event/agent_reasoning_raw_content"
    | "codex/event/agent_reasoning_raw_content_delta"
    | "codex/event/agent_reasoning_section_break"
    | "codex/event/agent_message"
    | "codex/event/agent_message_delta"
    | "codex/event/agent_message_content_delta"
    | "codex/event/raw_response_item"
    | "codex/event/reasoning_content_delta"
    | "codex/event/reasoning_raw_content_delta"
    // Tool Calls
    | "codex/event/mcp_tool_call_begin"
    | "codex/event/mcp_tool_call_end"
    | "codex/event/mcp_startup_update"
    | "codex/event/mcp_list_tools_response"
    | "codex/event/exec_command_begin"
    | "codex/event/exec_command_end"
    | "codex/event/exec_command_output_delta"
    | "codex/event/exec_approval_request"
    | "codex/event/apply_patch_approval_request"
    | "codex/event/patch_apply_begin"
    | "codex/event/patch_apply_end"
    // Plan / Review
    | "codex/event/plan_delta"
    | "codex/event/plan_update"
    | "codex/event/entered_review_mode"
    | "codex/event/exited_review_mode"
    | "codex/event/item_started"
    | "codex/event/item_completed"
    // Skills / Plugins
    | "codex/event/list_skills_response"
    | "codex/event/list_remote_skills_response"
    | "codex/event/list_custom_prompts_response"
    | "codex/event/remote_skill_downloaded"
    // Other
    | "codex/event/web_search_begin"
    | "codex/event/web_search_end"
    | "codex/event/view_image_tool_call"
    | "codex/event/background_event"
    | "codex/event/user_message"
    | "codex/event/get_history_entry_response";

export interface BackendMessage {
    type: "status" | "response" | "text" | "error" | "done" | "cancelled" | "plan" | SSEEventType;
    content?: string;
    sessionId?: string;
    plan?: PlanStep[];
    diffs?: string[];
    data?: Record<string, any>;
    response?: string;
}

export interface ThreadFollowerState {
    activeThreadId: string | null;
    status: "idle" | "running" | "interrupted" | "compacting" | "completed";
    summary: string;
    queuedFollowups: string[];
    settings: {
        model: string;
        reasoning_effort: "low" | "medium" | "high" | "xhigh";
        sandbox_policy: string;
        approval_mode: string;
    } | null;
}

export interface FileEntry {
    name: string;
    isDirectory: boolean;
    isFile: boolean;
}

export interface ThemeColors {
    bg: string;
    bgSecondary: string;
    surface: string;
    border: string;
    text: string;
    textSecondary: string;
    accent: string;
    accentHover: string;
    error: string;
    success: string;
    warning: string;
}

export const lightTheme: ThemeColors = {
    bg: "#ffffff",
    bgSecondary: "#f6f8fa",
    surface: "#ffffff",
    border: "#d0d7de",
    text: "#1f2328",
    textSecondary: "#656d76",
    accent: "#0969da",
    accentHover: "#0550ae",
    error: "#cf222e",
    success: "#1a7f37",
    warning: "#9a6700",
};

export const darkTheme: ThemeColors = {
    bg: "#0d1117",
    bgSecondary: "#161b22",
    surface: "#161b22",
    border: "#30363d",
    text: "#e6edf3",
    textSecondary: "#8b949e",
    accent: "#58a6ff",
    accentHover: "#79c0ff",
    error: "#f85149",
    success: "#3fb950",
    warning: "#d29922",
};
