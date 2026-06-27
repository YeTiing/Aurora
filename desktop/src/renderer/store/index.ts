// Aurora Zustand 状态管理 — 集成 IndexedDB 持久化
import { create } from "zustand";
import type { Session, AgentMessage, PlanStep, ThemeColors, FileEntry, ToolLog, ThreadFollowerState, ApprovalRequestState, SharedObjectSnapshot } from "../../shared/types";
import { darkTheme, lightTheme } from "../../shared/types";
import * as db from "./db";

function uid(): string {
    return Math.random().toString(36).substring(2, 10);
}

interface AuroraState {
    // Sessions
    sessions: Session[];
    activeSessionId: string | null;
    sessionsLoaded: boolean;
    loadSessions: () => Promise<void>;
    createSession: (workspace?: string) => string;
    setActiveSession: (id: string) => void;
    addMessage: (sessionId: string, msg: Omit<AgentMessage, "id" | "timestamp">) => void;
    updatePlan: (sessionId: string, plan: PlanStep[]) => void;
    updatePlanStep: (sessionId: string, stepIndex: number, status: PlanStep["status"]) => void;
    addToolLog: (sessionId: string, log: Omit<ToolLog, "timestamp">) => void;
    deleteSession: (id: string) => void;
    renameSession: (id: string, title: string) => void;
    duplicateSession: (id: string) => string;
    togglePinSession: (id: string) => void;
    toggleArchiveSession: (id: string) => void;

    // LLM Settings
    llmProvider: string;
    llmModel: string;
    llmApiKey: string;
    llmBaseUrl: string;
    llmMaxContext: number;
    llmTemperature: number;
    setLLMProvider: (v: string) => void;
    setLLMModel: (v: string) => void;
    setLLMApiKey: (v: string) => void;
    setLLMBaseUrl: (v: string) => void;
    setLLMMaxContext: (v: number) => void;
    setLLMTemperature: (v: number) => void;
    reloadLLMSettings: () => Promise<void>;

    // Backend
    backendConnected: boolean;
    setBackendConnected: (v: boolean) => void;
    isStreaming: boolean;
    setStreaming: (v: boolean) => void;

    // Codex Thread Follower
    threadFollower: ThreadFollowerState;
    updateThreadFollower: (patch: Partial<ThreadFollowerState>) => void;

    // Codex Approval
    approvals: ApprovalRequestState[];
    upsertApproval: (approval: ApprovalRequestState) => void;
    updateApprovalStatus: (requestId: string, status: ApprovalRequestState["status"]) => void;

    // Codex Shared Objects
    sharedObjects: SharedObjectSnapshot;
    setSharedObjects: (snapshot: SharedObjectSnapshot) => void;
    setSharedObject: (key: string, value: unknown) => void;

    // Theme
    theme: "dark" | "light";
    themeColors: ThemeColors;
    toggleTheme: () => void;

    // UI Layout
    leftPanelWidth: number;
    rightPanelWidth: number;
    terminalHeight: number;
    setLeftPanelWidth: (w: number) => void;
    setRightPanelWidth: (w: number) => void;
    setTerminalHeight: (h: number) => void;
    showFileTree: boolean; showSettings: boolean;
    toggleFileTree: () => void; toggleSettings: () => void; showSearch: boolean; toggleSearch: () => void; searchQuery: string; setSearchQuery: (q: string) => void;
    showMonitor: boolean; toggleMonitor: () => void;
    showRightPanel: boolean; toggleRightPanel: () => void;
    terminalOpen: boolean; setTerminalOpen: (v: boolean) => void;

    // Editor
    openFiles: string[];
    activeFile: string | null;
    openFile: (path: string) => void;
    closeFile: (path: string) => void;
    setActiveFile: (path: string | null) => void;

    // Workspace
    editorFontSize: number;
    setEditorFontSize: (n: number) => void;
    editorTabSize: number;
    setEditorTabSize: (n: number) => void;
    editorWordWrap: boolean;
    setEditorWordWrap: (v: boolean) => void;
    editorMinimap: boolean;
    setEditorMinimap: (v: boolean) => void;
    terminalFontSize: number;
    setTerminalFontSize: (n: number) => void;
    workspace: string;
    setWorkspace: (path: string) => void;

    // Sandbox
    sandboxMode: "full-access" | "workspace-only" | "read-only";
    setSandboxMode: (mode: "full-access" | "workspace-only" | "read-only") => void;}

// 会话自动保存（防抖）
let saveTimer: ReturnType<typeof setTimeout> | null = null;
function debouncedSave(sessions: Session[]) {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
        db.saveAllSessions(sessions).catch(console.error);
    }, 1000);
}

export const useStore = create<AuroraState>((set, get) => ({
    sessions: [],
    activeSessionId: null,
    sessionsLoaded: false,

    async loadSessions() {
        try {
            const sessions = await db.loadAllSessions();
            const theme = await db.loadSetting("theme");
            const savedProvider = await db.loadSetting("llmProvider");
            const savedModel = await db.loadSetting("llmModel");
            const savedBaseUrl = await db.loadSetting("llmBaseUrl");
            const savedMaxContext = await db.loadSetting("llmMaxContext");
            const savedTemperature = await db.loadSetting("llmTemperature");
            const extraSettings: any = {};
            if (savedProvider) extraSettings.llmProvider = savedProvider;
            if (savedModel) extraSettings.llmModel = savedModel;
            if (savedBaseUrl) extraSettings.llmBaseUrl = savedBaseUrl;
            if (savedMaxContext) extraSettings.llmMaxContext = savedMaxContext;
            if (savedTemperature) extraSettings.llmTemperature = savedTemperature;
            if (sessions.length > 0) {
                set({
                    sessions,
                    activeSessionId: sessions[0].id,
                    sessionsLoaded: true,
                    theme: theme || "dark",
                    themeColors: theme === "light" ? lightTheme : darkTheme,
                    ...extraSettings,
                });
            } else {
                set({ sessionsLoaded: true, theme: theme || "dark",
                    themeColors: theme === "light" ? lightTheme : darkTheme,
                    ...extraSettings,
                });
            }
        } catch {
            set({ sessionsLoaded: true });
        }
    },

    createSession(workspace = ".") {
        const id = uid();
        const session: Session = {
            id, title: "New Chat", workspace,
            messages: [], plan: [], toolLogs: [],
            createdAt: Date.now(), updatedAt: Date.now(),
            pinned: false, archived: false,
        };
        set((s) => {
            const sessions = [...s.sessions, session];
            debouncedSave(sessions);
            return { sessions, activeSessionId: id };
        });
        return id;
    },

    setActiveSession(id) {
        set({ activeSessionId: id });
    },

    addMessage(sessionId, msg) {
        set((s) => {
            const sessions = s.sessions.map((ses) =>
                ses.id === sessionId
                    ? { ...ses, messages: [...ses.messages, { ...msg, id: uid(), timestamp: Date.now() }], updatedAt: Date.now() }
                    : ses
            );
            debouncedSave(sessions);
            return { sessions };
        });
    },

    updatePlan(sessionId, plan) {
        set((s) => {
            const sessions = s.sessions.map((ses) =>
                ses.id === sessionId ? { ...ses, plan } : ses
            );
            debouncedSave(sessions);
            return { sessions };
        });
    },

    updatePlanStep(sessionId, stepIndex, status) {
        set((s) => {
            const sessions = s.sessions.map((ses) => {
                if (ses.id !== sessionId) return ses;
                const plan = ses.plan.map((step: any, i: any) =>
                    i === stepIndex ? { ...step, status } : step
                );
                return { ...ses, plan, updatedAt: Date.now() };
            });
            debouncedSave(sessions);
            return { sessions };
        });
    },

    deleteSession(id) {
        set((s) => {
            const sessions = s.sessions.filter((ses) => ses.id !== id);
            debouncedSave(sessions);
            db.deleteSession(id).catch(console.error);
            return {
                sessions,
                activeSessionId: s.activeSessionId === id ? (sessions[0]?.id ?? null) : s.activeSessionId,
            };
        });
    },
    renameSession(id: string, title: string) {
        set((s) => {
            const sessions = s.sessions.map((ses) => ses.id === id ? { ...ses, title, updatedAt: Date.now() } : ses);
            debouncedSave(sessions);
            return { sessions };
        });
    },

    duplicateSession(id: string) {
        const state = get();
        const src = state.sessions.find((ses) => ses.id === id);
        if (!src) return "";
        const newId = Math.random().toString(36).substring(2, 10);
        const dup = {
            ...src,
            id: newId,
            title: (src.title || "Untitled") + " (Copy)",
            messages: src.messages.map((m) => ({ ...m })),
            plan: src.plan.map((p) => ({ ...p })),
            toolLogs: src.toolLogs.map((t) => ({ ...t })),
            createdAt: Date.now(),
            updatedAt: Date.now(),
        };
        const sessions = [...state.sessions, dup];
        set({ sessions });
        debouncedSave(sessions);
        return newId;
    },

    togglePinSession(id: string) {
        set((s) => {
            const sessions = s.sessions.map((ses) => ses.id === id ? { ...ses, pinned: !ses.pinned, updatedAt: Date.now() } : ses);
            debouncedSave(sessions);
            return { sessions };
        });
    },

    toggleArchiveSession(id: string) {
        set((s) => {
            const sessions = s.sessions.map((ses) => ses.id === id ? { ...ses, archived: !ses.archived, updatedAt: Date.now() } : ses);
            debouncedSave(sessions);
            return { sessions };
        });
    },

    llmProvider: "openai",
    llmModel: "gpt-4o",
    llmApiKey: "",
    llmBaseUrl: "https://api.openai.com/v1",
    llmMaxContext: 24000,
    llmTemperature: 0.3,
    setLLMProvider(v) { set({ llmProvider: v }); db.saveSetting("llmProvider", v).catch(() => {}); },
    setLLMModel(v) { set({ llmModel: v }); db.saveSetting("llmModel", v).catch(() => {}); },
    setLLMApiKey(v) { set({ llmApiKey: v }); },
    setLLMBaseUrl(v) { set({ llmBaseUrl: v }); db.saveSetting("llmBaseUrl", v).catch(() => {}); },
    setLLMMaxContext(v) { set({ llmMaxContext: v }); db.saveSetting("llmMaxContext", v).catch(() => {}); },
    setLLMTemperature(v) { set({ llmTemperature: v }); db.saveSetting("llmTemperature", v).catch(() => {}); },
    reloadLLMSettings: async () => {
        try {
            const r = await fetch("http://127.0.0.1:9876/settings");
            const s = await r.json();
            set({
                llmProvider: s.provider || "openai",
                llmModel: s.model || "gpt-4o",
                llmBaseUrl: s.base_url || "https://api.openai.com/v1",
                llmMaxContext: s.max_context_tokens || 24000,
                llmTemperature: s.temperature ?? 0.3,
            });
        } catch {}
    },

    backendConnected: false,
    setBackendConnected(v) { set({ backendConnected: v }); },
    isStreaming: false,
    setStreaming(v) { set({ isStreaming: v }); },
    threadFollower: {
        activeThreadId: null,
        status: "idle",
        summary: "",
        queuedFollowups: [],
        settings: null,
    },
    updateThreadFollower(patch) {
        set((s) => ({ threadFollower: { ...s.threadFollower, ...patch } }));
    },
    approvals: [],
    upsertApproval(approval) {
        set((s) => ({
            approvals: s.approvals.some((item) => item.id === approval.id)
                ? s.approvals.map((item) => item.id === approval.id ? { ...item, ...approval } : item)
                : [...s.approvals, approval],
        }));
    },
    updateApprovalStatus(requestId, status) {
        set((s) => ({
            approvals: s.approvals.map((item) => item.id === requestId ? { ...item, status } : item),
        }));
    },
    sharedObjects: {},
    setSharedObjects(snapshot) {
        set({ sharedObjects: { ...snapshot } });
    },
    setSharedObject(key, value) {
        set((s) => ({ sharedObjects: { ...s.sharedObjects, [key]: value } }));
    },
    addToolLog(sessionId, log) { set((s) => { const sessions = s.sessions.map((ses) => ses.id === sessionId ? { ...ses, toolLogs: [...(ses.toolLogs || []), { ...log, timestamp: Date.now() }], updatedAt: Date.now() } : ses); return { sessions }; }); },

    theme: "dark",
    themeColors: darkTheme,
    toggleTheme() {
        set((s) => {
            const theme = s.theme === "dark" ? "light" : "dark";
            const themeColors = theme === "light" ? lightTheme : darkTheme;
            db.saveSetting("theme", theme).catch(console.error);
            return { theme, themeColors };
        });
    },

    leftPanelWidth: 220,
    rightPanelWidth: 380,
    terminalHeight: 250,
    setLeftPanelWidth(w) { set({ leftPanelWidth: w }); },
    setRightPanelWidth(w) { set({ rightPanelWidth: w }); },
    setTerminalHeight(h) { set({ terminalHeight: h }); },
    showFileTree: true, showSettings: false, showSearch: false, searchQuery: '', showMonitor: false,
    toggleFileTree() { set((s) => ({ showFileTree: !s.showFileTree })); }, toggleSettings() { set((s) => ({ showSettings: !s.showSettings })); }, toggleSearch() { set((s) => ({ showSearch: !s.showSearch })); }, toggleMonitor() { set((s) => ({ showMonitor: !s.showMonitor })); }, setSearchQuery(q) { set({ searchQuery: q }); },
    showRightPanel: true,
    toggleRightPanel() { set((s) => ({ showRightPanel: !s.showRightPanel })); },
    terminalOpen: false,
    setTerminalOpen(v) { set({ terminalOpen: v }); },

    openFiles: [],
    activeFile: null,
    openFile(path) {
        set((s) => ({
            openFiles: s.openFiles.includes(path) ? s.openFiles : [...s.openFiles, path],
            activeFile: path,
        }));
    },
    closeFile(path) {
        set((s) => {
            const files = s.openFiles.filter((f) => f !== path);
            return {
                openFiles: files,
                activeFile: s.activeFile === path ? (files[files.length - 1] ?? null) : s.activeFile,
            };
        });
    },
    setActiveFile(path) { set({ activeFile: path }); },

    editorFontSize: 14,
    setEditorFontSize(n) { set({ editorFontSize: n }); db.saveSetting("editorFontSize", n).catch(() => {}); },
    editorTabSize: 4,
    setEditorTabSize(n) { set({ editorTabSize: n }); db.saveSetting("editorTabSize", n).catch(() => {}); },
    editorWordWrap: false,
    setEditorWordWrap(v) { set({ editorWordWrap: v }); db.saveSetting("editorWordWrap", v).catch(() => {}); },
    editorMinimap: false,
    setEditorMinimap(v) { set({ editorMinimap: v }); db.saveSetting("editorMinimap", v).catch(() => {}); },
    terminalFontSize: 13,
    setTerminalFontSize(n) { set({ terminalFontSize: n }); db.saveSetting("terminalFontSize", n).catch(() => {}); },
    workspace: ".",
    setWorkspace(path) { set({ workspace: path }); },

    sandboxMode: "full-access",
    setSandboxMode(mode) { set({ sandboxMode: mode }); },

  // ── Slash Commands ──
  getSlashCommands: (): {cmd: string; desc: string}[] => ([
    { cmd: "/memory", desc: "Show memory stats" },
    { cmd: "/memory-list", desc: "List all memory entries" },
    { cmd: "/memory-search", desc: "Search past sessions" },
    { cmd: "/skill-list", desc: "List all skills" },
    { cmd: "/skill-create", desc: "Create a new skill" },
    { cmd: "/cron-list", desc: "List cron tasks" },
    { cmd: "/cron-add", desc: "Add a cron task" },
    { cmd: "/soul", desc: "View or edit SOUL.md" },
    { cmd: "/plan", desc: "Create a step-by-step plan" },
    { cmd: "/undo", desc: "Undo last action" },
    { cmd: "/stats", desc: "Show session statistics" },
  ]),
  handleSlashCommand: (cmd: string, input: string): string => {
    const mapping: Record<string, string> = {
      "/memory": "memory action='stats'",
      "/memory-list": "memory action='list'",
      "/memory-search": `memory action='search' text='${input}'`,
      "/skill-list": "memory action='skill_list'",
      "/skill-create": "memory action='skill_create' text='${input}'",
      "/cron-list": "cron action='list'",
      "/cron-add": `cron action='add' ${input}`,
      "/soul": "View current SOUL.md personality",
      "/plan": `Please create a step-by-step plan for: ${input}`,
      "/undo": "Undo the last action and revert changes",
      "/stats": "memory action='stats'",
    };
    return mapping[cmd] || input;
  },

}));