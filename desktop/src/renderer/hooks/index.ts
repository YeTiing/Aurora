// Aurora hooks 鈥?Agent SSE闃熶紶 + 缁堢鎺у埗 (Codex SSE浜嬩欢瀵归綈)
import { useEffect, useRef, useCallback } from "react";
import { useStore } from "../store";
import type { BackendMessage, SSEEvent } from "../../shared/types";

declare global {
    interface Window {
        aurora: {
            chat: (data: { message: string; workspace: string; sessionId: string; sandboxMode?: string; model?: string; history?: {role:string;content:string}[] }) => Promise<any>;
            cancel: (sessionId: string) => Promise<any>;
            threadControl: (data: any) => Promise<any>;
            approvalDecision: (data: { requestId: string; action: "approve" | "deny"; sessionId?: string; threadId?: string }) => Promise<any>;
            terminal: {
                create: (sessionId: string, cwd: string) => Promise<boolean>;
                write: (sessionId: string, data: string) => Promise<any>;
                resize: (sessionId: string, cols: number, rows: number) => Promise<any>;
                kill: (sessionId: string) => Promise<any>;
                onData: (cb: (data: { sessionId: string; data: string }) => void) => void;
                onExit: (cb: (data: { sessionId: string; exitCode: number }) => void) => void;
            };
            onAgentMessage: (cb: (msg: any) => void) => (() => void);
            onBackendConnected: (cb: () => void) => (() => void);
            dialog: {
                openFolder: () => Promise<string | null>;
                openFile: () => Promise<string | null>;
            };
            file: {
                read: (path: string) => Promise<string | { error: string }>;
                write: (path: string, content: string) => Promise<any>;
                list: (path: string) => Promise<{ name: string; isDirectory: boolean; isFile: boolean }[] | { error: string }>;
            };
            openExternal: (url: string) => Promise<void>;
            searchInFiles: (params: any) => Promise<any>;
            platform: string;
        };
    }
}

function normalizeEventType(rawType: string): string {
    if (rawType.startsWith("codex/event/")) return rawType;
    const mapping: Record<string, string> = {
        "status": "codex/event/agent_reasoning",
        "plan": "codex/event/plan_update",
        "tool_call": "codex/event/exec_command_begin",
        "tool_result": "codex/event/exec_command_end",
        "response": "codex/event/agent_message",
        "error": "codex/event/error",
        "done": "codex/event/task_complete",
        "cancelled": "codex/event/turn_aborted",
        "text": "codex/event/agent_message_content_delta",
    };
    return mapping[rawType] || rawType;
}

export function useAgent() {
    const addMessage = useStore((s) => s.addMessage);
    const addToolLog = useStore((s) => s.addToolLog);
    const updatePlan = useStore((s) => s.updatePlan);
    const updateThreadFollower = useStore((s) => s.updateThreadFollower);
    const upsertApproval = useStore((s) => s.upsertApproval);
    const updateApprovalStatus = useStore((s) => s.updateApprovalStatus);
    const setStreaming = useStore((s) => s.setStreaming);
    const setBackendConnected = useStore((s) => s.setBackendConnected);
    const streamingRef = useRef(false);
    // Deduplicate assistant messages by content hash
    const seenMessages = useRef(new Set<string>());

    useEffect(() => {
        const unsub = window.aurora?.onAgentMessage((raw: any) => {
            const sessionId = useStore.getState().activeSessionId;
            if (!sessionId) return;

            const eventType = raw.type ? normalizeEventType(raw.type) : "";
            const data = raw.data || raw;

            switch (eventType) {
                case "codex/event/exec_approval_request":
                    if (!data.request_id && !data.id) break;
                    upsertApproval({
                        id: data.request_id || data.id,
                        type: "command",
                        risk: data.risk || "high",
                        description: data.description || data.command || "Command approval requested",
                        command: data.command,
                        status: "pending",
                    });
                    break;

                case "codex/event/apply_patch_approval_request":
                    if (!data.request_id && !data.id) break;
                    upsertApproval({
                        id: data.request_id || data.id,
                        type: "file",
                        risk: data.risk || "medium",
                        description: data.description || data.file_path || "File approval requested",
                        filePath: data.file_path,
                        status: "pending",
                    });
                    break;

                case "codex/event/thread_follower_command_approval_decision":
                case "codex/event/thread_follower_file_approval_decision":
                    if (data.ok && data.request_id) {
                        updateApprovalStatus(data.request_id, data.decision === "approve" ? "approved" : "denied");
                    }
                    break;

                case "codex/event/thread_follower_start_turn":
                    updateThreadFollower({
                        activeThreadId: raw.thread_id || data.thread_id || null,
                        status: "running",
                        settings: data.settings || null,
                    });
                    break;

                case "codex/event/thread_follower_steer_turn":
                    updateThreadFollower({
                        activeThreadId: raw.thread_id || data.thread_id || null,
                    });
                    break;

                case "codex/event/thread_follower_interrupt_turn":
                    updateThreadFollower({
                        activeThreadId: raw.thread_id || data.thread_id || null,
                        status: "interrupted",
                    });
                    break;

                case "codex/event/thread_follower_compact_thread":
                    updateThreadFollower({
                        activeThreadId: raw.thread_id || data.thread_id || null,
                        status: data.compacted === false ? "running" : "compacting",
                        summary: data.summary || "",
                    });
                    break;

                case "codex/event/thread_follower_set_queued_followups_state":
                    updateThreadFollower({
                        activeThreadId: raw.thread_id || data.thread_id || null,
                        queuedFollowups: data.queued_followups || [],
                    });
                    break;

                case "codex/event/thread_follower_update_thread_settings":
                    updateThreadFollower({
                        activeThreadId: raw.thread_id || data.thread_id || null,
                        settings: data.settings || null,
                    });
                    break;

                case "codex/event/task_started":
                    streamingRef.current = true;
                    setStreaming(true);
                    break;

                case "codex/event/agent_reasoning":
                case "codex/event/agent_reasoning_delta":
                    if (data.status) {
                        addMessage(sessionId, { role: "system", content: `馃 ${data.status}` });
                    }
                    break;

                case "codex/event/plan_update":
                    if (data.plan) updatePlan(sessionId, data.plan);
                    break;

                case "codex/event/exec_command_begin":
                    addToolLog(sessionId, {
                        type: "tool_start",
                        tool: data.tool || data.name || "unknown",
                        toolCallId: data.tool_call_id || data.toolCallId || "",
                        args: data.args || data.arguments || {},
                    });
                    break;

                case "codex/event/exec_command_end":
                    addToolLog(sessionId, {
                        type: "tool_end",
                        tool: data.tool || data.name || "unknown",
                        toolCallId: data.tool_call_id || data.toolCallId || "",
                        success: data.success,
                        output: data.output || "",
                        error: data.error || "",
                    });
                    break;

                case "codex/event/agent_message":
                case "codex/event/agent_message_content_delta":
                    if (data.content || data.delta) {
                        const msg = data.content || data.delta || "";
                        const key = sessionId + ":" + msg.slice(0, 80);
                        if (!seenMessages.current.has(key)) {
                            seenMessages.current.add(key);
                            addMessage(sessionId, { role: "assistant", content: msg });
                        }
                    }
                    break;

                case "codex/event/task_complete":
                    seenMessages.current.clear();
                    setStreaming(false);
                    streamingRef.current = false;
                    if (data.plan) updatePlan(sessionId, data.plan);
                    updateThreadFollower({ status: "completed" });
                    break;

                case "codex/event/error":
                case "codex/event/stream_error":
                    addMessage(sessionId, { role: "system", content: `鉂?${data.error || "Unknown error"}` });
                    setStreaming(false);
                    break;

                case "codex/event/turn_aborted":
                    seenMessages.current.clear();
                    setStreaming(false);
                    streamingRef.current = false;
                    break;

                default:
                    if (raw.type === "done") {
                        seenMessages.current.clear();
                        setStreaming(false);
                        updateThreadFollower({ status: "completed" });
                    } else if (raw.type === "error" && raw.content) {
                        addMessage(sessionId, { role: "system", content: `鉂?${raw.content}` });
                        setStreaming(false);
                    }
                    break;
            }
        });

        const unsub2 = window.aurora?.onBackendConnected(() => {
            setBackendConnected(true);
        });

        return () => {
            unsub?.();
            unsub2?.();
        };
    }, []);  // Empty deps - only run once

    const sendMessage = useCallback(async (message: string) => {
        const state = useStore.getState();
        let sessionId = state.activeSessionId;
        if (!sessionId) {
            sessionId = state.createSession(state.workspace);
        }
        // Build history BEFORE adding current message (only user/assistant roles)
        const session = state.sessions.find((s: any) => s.id === sessionId);
        const history = (session?.messages || [])
            .filter((m: any) => m.role === "user" || m.role === "assistant")
            .slice(-8)
            .map((m: any) => ({ role: m.role, content: m.content }));
        addMessage(sessionId, { role: "user", content: message });
        setStreaming(true);
        await window.aurora?.chat({ message, workspace: state.workspace, sessionId, sandboxMode: state.sandboxMode, model: state.llmModel, history });
    }, [addMessage, setStreaming]);

    const controlThread = useCallback(async (action: string, payload: Record<string, any> = {}) => {
        const state = useStore.getState();
        const threadId = state.threadFollower.activeThreadId || state.activeSessionId;
        if (!threadId) return { sent: false, error: "No active thread" };
        return window.aurora?.threadControl({
            action,
            threadId,
            sessionId: state.activeSessionId || threadId,
            ...payload,
        });
    }, []);

    const steerThread = useCallback((instruction: string) =>
        controlThread("steer", { instruction }), [controlThread]);

    const compactThread = useCallback((tokenUsageRatio = 0.9) =>
        controlThread("compact", { tokenUsageRatio }), [controlThread]);

    const updateThreadSettings = useCallback((settings: Record<string, any>) =>
        controlThread("settings", settings), [controlThread]);

    const setQueuedFollowups = useCallback((followups: string[]) =>
        controlThread("followups", { followups }), [controlThread]);

    const decideApproval = useCallback(async (requestId: string, action: "approve" | "deny") => {
        const state = useStore.getState();
        const result = await window.aurora?.approvalDecision({
            requestId,
            action,
            sessionId: state.activeSessionId || undefined,
            threadId: state.threadFollower.activeThreadId || state.activeSessionId || undefined,
        });
        if (result?.sent) {
            updateApprovalStatus(requestId, action === "approve" ? "approved" : "denied");
        }
        return result;
    }, [updateApprovalStatus]);

    const cancelRequest = useCallback(async () => {
        seenMessages.current.clear();
        const sessionId = useStore.getState().activeSessionId;
        if (sessionId) {
            await window.aurora?.cancel(sessionId);
            setStreaming(false);
        }
    }, [setStreaming]);

    return { sendMessage, cancelRequest, steerThread, compactThread, updateThreadSettings, setQueuedFollowups, decideApproval };
}

export function useTerminal(sessionId: string, cwd: string) {
    const created = useRef(false);
    useEffect(() => {
        if (!created.current) {
            window.aurora?.terminal.create(sessionId, cwd);
            created.current = true;
        }
        return () => { created.current = false; };
    }, [sessionId, cwd]);

    const write = useCallback((data: string) => {
        window.aurora?.terminal.write(sessionId, data);
    }, [sessionId]);

    const resize = useCallback((cols: number, rows: number) => {
        window.aurora?.terminal.resize(sessionId, cols, rows);
    }, [sessionId]);

    const kill = useCallback(() => {
        window.aurora?.terminal.kill(sessionId);
    }, [sessionId]);

    return { write, resize, kill };
}

export function useTheme() {
    const themeColors = useStore((s) => s.themeColors);
    return themeColors;
}
