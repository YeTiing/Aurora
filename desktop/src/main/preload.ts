import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("aurora", {
    // Agent
    chat: (data: { message: string; workspace: string; sessionId: string; sandboxMode?: string; model?: string; history?: {role:string;content:string}[] }) =>
        ipcRenderer.invoke("agent:chat", data),
    cancel: (sessionId: string) =>
        ipcRenderer.invoke("agent:cancel", { sessionId }),
    threadControl: (data: any) =>
        ipcRenderer.invoke("agent:threadControl", data),
    approvalDecision: (data: { requestId: string; action: "approve" | "deny"; sessionId?: string; threadId?: string }) =>
        ipcRenderer.invoke("agent:approvalDecision", data),
    sharedObjects: {
        snapshot: () => ipcRenderer.invoke("sharedObjects:snapshot"),
        set: (key: string, value: unknown, source?: string) =>
            ipcRenderer.invoke("sharedObjects:set", { key, value, source }),
    },

    // Terminal
    terminal: {
        create: (sessionId: string, cwd: string) =>
            ipcRenderer.invoke("terminal:create", { sessionId, cwd }),
        write: (sessionId: string, data: string) =>
            ipcRenderer.invoke("terminal:write", { sessionId, data }),
        resize: (sessionId: string, cols: number, rows: number) =>
            ipcRenderer.invoke("terminal:resize", { sessionId, cols, rows }),
        kill: (sessionId: string) =>
            ipcRenderer.invoke("terminal:kill", { sessionId }),
        onData: (callback: (data: { sessionId: string; data: string }) => void) =>
            ipcRenderer.on("terminal:data", (_e, d) => callback(d)),
        onExit: (callback: (data: { sessionId: string; exitCode: number }) => void) =>
            ipcRenderer.on("terminal:exit", (_e, d) => callback(d)),
    },

    // Agent events
    onAgentMessage: (callback: (msg: any) => void) => {
        const handler = (_e: any, msg: any) => callback(msg);
        ipcRenderer.on("agent:message", handler);
        return () => ipcRenderer.removeListener("agent:message", handler);
    },
    onBackendConnected: (callback: () => void) => {
        const handler = () => callback();
        ipcRenderer.on("backend:connected", handler);
        return () => ipcRenderer.removeListener("backend:connected", handler);
    },

    // Dialogs
    dialog: {
        openFolder: () => ipcRenderer.invoke("dialog:openFolder"),
        openFile: () => ipcRenderer.invoke("dialog:openFile"),
    },

    // File ops
    file: {
        read: (path: string) => ipcRenderer.invoke("file:read", path),
        write: (path: string, content: string) =>
            ipcRenderer.invoke("file:write", { filePath: path, content }),
        list: (dirPath: string) => ipcRenderer.invoke("file:list", dirPath),
    },

    // Search
    searchInFiles: (params: any) =>
        ipcRenderer.invoke("search:inFiles", params),

    // Browser View
    browser: {
        open: (url: string) => ipcRenderer.invoke("browser:open", url),
        close: () => ipcRenderer.invoke("browser:close"),
        navigate: (url: string) => ipcRenderer.invoke("browser:navigate", url),
        back: () => ipcRenderer.invoke("browser:back"),
        forward: () => ipcRenderer.invoke("browser:forward"),
        reload: () => ipcRenderer.invoke("browser:reload"),
        getState: () => ipcRenderer.invoke("browser:getState"),
        onState: (callback: (data: any) => void) => {
            const handler = (_e: any, d: any) => callback(d);
            ipcRenderer.on("browser:state", handler);
            return () => ipcRenderer.removeListener("browser:state", handler);
        },
        onNavigated: (callback: (data: any) => void) => {
            const handler = (_e: any, d: any) => callback(d);
            ipcRenderer.on("browser:navigated", handler);
            return () => ipcRenderer.removeListener("browser:navigated", handler);
        },
    },

    // Shell
    openExternal: (url: string) => ipcRenderer.invoke("shell:openExternal", url),

    // Platform
    platform: process.platform,
});
