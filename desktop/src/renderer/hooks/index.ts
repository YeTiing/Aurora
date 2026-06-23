// Aurora hooks — Agent SSE通信 + 终端控制 (Codex SSE事件对齐)
import { useEffect, useRef, useCallback } from "react";
import { useStore } from "../store";
import type { BackendMessage, SSEEvent } from "../../shared/types";

declare global {
    interface Window {
        aurora: {
            chat: (data: { message: string; workspace: string; sessionId: string; sandboxMode?: string; model?: string }) => Promise<any>;
            cancel: (sessionId: string) => Promise<any>;
            terminal: {
                create: (sessionId: string, cwd: string) => Promise<boolean>;
                write: (sessionId: string, data: string) => Promise<any>;
                resize: (sessionId: string, cols: number, rows: number) => Promise<any>;
                kill: (sessionId: string) => Promise<any>;
                onData: (cb: (data: { sessionId: string; data: string }) => void) => void;
                onExit: (cb: (data: { sessionId: string; exitCode: number }) => void) => void;
            };
            onAgentMessage: (cb: (msg: any) => void) => void;
            onBackendConnected: (cb: () => void) => void;
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

// 处理 Codex 格式的 SSE 事件名称
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
    const setStreaming = useStore((s) => s.setStreaming);
    const setBackendConnected = useStore((s) => s.setBackendConnected);
    const streamingRef = useRef(false);

    useEffect(() => {
        window.aurora?.onAgentMessage((raw: any) => {
            const sessionId = useStore.getState().activeSessionId;
            if (!sessionId) return;

            //  SSE 
            const eventType = raw.type ? normalizeEventType(raw.type) : "";
            const data = raw.data || raw;

            switch (eventType) {
                case "codex/event/task_started":
                    addMessage(sessionId, { role: "system", content: `🚀 ${data.task || ""}`.trim() });
                    streamingRef.current = true;
                    setStreaming(true);
                    break;

                case "codex/event/agent_reasoning":
                case "codex/event/agent_reasoning_delta":
                    if (data.status) {
                        addMessage(sessionId, { role: "system", content: `🧠 ${data.status}` });
                    }
                    if (data.delta && data.delta.length < 200) {
                        addMessage(sessionId, { role: "system", content: data.delta });
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
                case "codex/event/agent_message_delta":
                case "codex/event/agent_message_content_delta":
                    if (data.content || data.delta) {
                        addMessage(sessionId, {
                            role: "assistant",
                            content: data.content || data.delta || "",
                        });
                    }
                    if (raw.type === "response" && data.content) {
                        setStreaming(false);
                        if (data.plan) updatePlan(sessionId, data.plan);
                    }
                    break;

                case "codex/event/task_complete":
                    setStreaming(false);
                    streamingRef.current = false;
                    if (data.result) {
                        addMessage(sessionId, { role: "system", content: `✅ 完成` });
                    }
                    if (data.plan) updatePlan(sessionId, data.plan);
                    if (raw.response) {
                        addMessage(sessionId, { role: "assistant", content: raw.response });
                    }
                    break;

                case "codex/event/error":
                case "codex/event/stream_error":
                    addMessage(sessionId, { role: "system", content: `❌ ${data.error || raw.content || "Unknown error"}` });
                    setStreaming(false);
                    break;

                case "codex/event/warning":
                    addMessage(sessionId, { role: "system", content: `⚠️ ${data.warning || ""}` });
                    break;

                case "codex/event/turn_aborted":
                    setStreaming(false);
                    streamingRef.current = false;
                    addMessage(sessionId, { role: "system", content: "⏹ 已取消" });
                    break;

                default:
                    // Legacy 兼容
                    if (raw.type === "status" && raw.content) {
                        addMessage(sessionId, { role: "system", content: `[${raw.content}]` });
                    } else if (raw.type === "response" && raw.content) {
                        addMessage(sessionId, { role: "assistant", content: raw.content });
                        setStreaming(false);
                    } else if (raw.type === "error" && raw.content) {
                        addMessage(sessionId, { role: "system", content: `❌ ${raw.content}` });
                        setStreaming(false);
                    } else if (raw.type === "done") {
                        setStreaming(false);
                    }
                    break;
            }
        });

        window.aurora?.onBackendConnected(() => {
            setBackendConnected(true);
        });
    }, [addMessage, addToolLog, updatePlan, setStreaming, setBackendConnected]);

    const sendMessage = useCallback(async (message: string) => {
        const state = useStore.getState();
        let sessionId = state.activeSessionId;
        if (!sessionId) {
            sessionId = state.createSession(state.workspace);
        }
        addMessage(sessionId, { role: "user", content: message });
        setStreaming(true);
        await window.aurora?.chat({ message, workspace: state.workspace, sessionId, sandboxMode: state.sandboxMode, model: state.llmModel });
    }, [addMessage, setStreaming]);

    const cancelRequest = useCallback(async () => {
        const sessionId = useStore.getState().activeSessionId;
        if (sessionId) {
            await window.aurora?.cancel(sessionId);
            setStreaming(false);
        }
    }, [setStreaming]);

    return { sendMessage, cancelRequest };
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