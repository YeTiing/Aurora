import { app, BrowserWindow, BrowserView, Tray, Menu, Notification, ipcMain, dialog, shell, nativeImage } from "electron";
import * as path from "path";
import * as fs from "fs";
import { spawn, ChildProcess } from "child_process";
import WebSocket from "ws";

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let ws: WebSocket | null = null;
let backendProcess: ChildProcess | null = null;
let minimizeToTray = true;
let isQuitting = false;
let browserView: BrowserView | null = null;
let browserViewVisible = false;

const BACKEND_URL = "ws://127.0.0.1:9876";
let browserControlledByAI = false;

// Check if --minimized flag was passed
const startMinimized = process.argv.includes("--minimized");

// Create system tray
function createTray() {
    // Create a 16x16 tray icon (simple colored square as fallback)
    const iconSize = 16;
    const canvas = Buffer.alloc(iconSize * iconSize * 4);
    for (let i = 0; i < iconSize * iconSize; i++) {
        const offset = i * 4;
        // Aurora accent blue-ish color
        canvas[offset] = 88;     // R
        canvas[offset + 1] = 166; // G
        canvas[offset + 2] = 255; // B
        canvas[offset + 3] = 255; // A
    }
    const icon = nativeImage.createFromBuffer(canvas, { width: iconSize, height: iconSize });

    tray = new Tray(icon);
    tray.setToolTip("Aurora");

    const contextMenu = Menu.buildFromTemplate([
        {
            label: "Show/Hide",
            click: () => {
                if (mainWindow) {
                    if (mainWindow.isVisible()) {
                        mainWindow.hide();
                    } else {
                        mainWindow.show();
                        mainWindow.focus();
                    }
                }
            },
        },
        {
            label: "New Session",
            click: () => {
                if (mainWindow) {
                    mainWindow.show();
                    mainWindow.focus();
                    mainWindow.webContents.send("shortcut:newSession");
                }
            },
        },
        { type: "separator" },
        {
            label: "Quit",
            click: () => {
                isQuitting = true;
                app.quit();
            },
        },
    ]);

    tray.setContextMenu(contextMenu);

    tray.on("double-click", () => {
        if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
        }
    });
}

// Show notification
function showNotification(title: string, body: string) {
    if (Notification.isSupported()) {
        const notification = new Notification({ title, body });
        notification.show();
    }
}

// Window creation
function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1400,
        height: 900,
        minWidth: 900,
        minHeight: 600,
        title: "Aurora",
        // titleBarStyle: "hiddenInset" (macOS only),
        backgroundColor: "#0d1117",
        frame: true,
        show: !startMinimized,
        webPreferences: {
            preload: path.join(__dirname, "preload.js"),
            nodeIntegration: false,
            contextIsolation: true,
            // keep webSecurity on; local wallpaper images allowed via CSP img-src file: data:
        },
    });

    console.log("[Aurora] NODE_ENV:", process.env.NODE_ENV);
    console.log("[Aurora] args:", process.argv.filter(a => a.startsWith("-")));
    console.log("[Aurora] isDev:", process.env.NODE_ENV === "development" || process.argv.includes("--dev"));

    mainWindow.webContents.on("did-fail-load", (_e, code, desc, url) => {
        console.error("[Aurora] PAGE LOAD FAILED:", code, desc, url);
    });
    mainWindow.webContents.on("console-message", (_e, _lvl, msg) => {
        // Do nothing to avoid EPIPE crashes
    });

    if (process.env.NODE_ENV === "development" || process.argv.includes("--dev")) {
        console.log("[Aurora] Loading dev URL: http://localhost:5173");
        mainWindow.loadURL("http://localhost:5173");
        mainWindow.webContents.openDevTools({ mode: "detach" });
    } else {
        const prodPath = path.join(__dirname, "../renderer/index.html");
        console.log("[Aurora] Loading production file:", prodPath);
        mainWindow.loadFile(prodPath);
    }

    // Minimize to tray instead of closing
    mainWindow.on("close", (event) => {
        if (!isQuitting && minimizeToTray) {
            event.preventDefault();
            mainWindow?.hide();
            return;
        }
    });

    mainWindow.on("resize", () => {
        if (browserView && browserViewVisible) {
            const bounds = mainWindow!.getContentBounds();
            const bvWidth = Math.floor(bounds.width * 0.45);
            browserView.setBounds({
                x: bounds.width - bvWidth,
                y: 60,
                width: bvWidth,
                height: bounds.height - 60,
            });
        }
    });

mainWindow.on("closed", () => { mainWindow = null; });

    // If minimized flag, don't show initially
    if (startMinimized && mainWindow) {
        mainWindow.hide();
    }
}

// Backend connection
// ─── Browser command handler (uses native Electron APIs, NO debugger needed) ───

async function ensureBrowserViewForAI(url?: string) {
    if (!mainWindow) return false;
    if (!browserView) {
        browserView = new BrowserView({
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true,
                sandbox: true,
            },
        });
        mainWindow.addBrowserView(browserView);
        const bounds = mainWindow.getContentBounds();
        const bvWidth = Math.floor(bounds.width * 0.45);
        browserView.setBounds({
            x: bounds.width - bvWidth,
            y: 60,
            width: bvWidth,
            height: bounds.height - 60,
        });
        browserView.setAutoResize({ width: true, height: true, horizontal: true, vertical: true });
        browserView.webContents.on("did-navigate", (_e, navUrl) => {
            mainWindow?.webContents.send("browser:navigated", { url: navUrl });
        });
    }
    browserViewVisible = true;
    browserControlledByAI = true;
    mainWindow.webContents.send("browser:ai_control", { active: true });
    if (url && url !== "about:blank") {
        await browserView.webContents.loadURL(url);
    }
    return true;
}

async function handleBrowserCommand(msg: { id: string; method: string; params: any }) {
    const { id, method, params } = msg;
    const sendResult = (result: any) => {
        if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "browser_result", id, result }));
        }
    };

    try {
        switch (method) {
            case "open": {
                await ensureBrowserViewForAI(params?.url || "about:blank");
                sendResult({ success: true, url: browserView?.webContents.getURL() || "" });
                break;
            }
            case "navigate": {
                if (!browserView) await ensureBrowserViewForAI();
                if (params?.url) {
                    await browserView?.webContents.loadURL(params.url);
                }
                sendResult({ success: true, url: browserView?.webContents.getURL() || "" });
                break;
            }
            case "screenshot": {
                if (!browserView) { sendResult({ error: "BrowserView not open" }); break; }
                try {
                    const image = await browserView.webContents.capturePage();
                    const png = image.toPNG();
                    const base64 = png.toString("base64");
                    sendResult({
                        data_url: "data:image/png;base64," + base64,
                        width: image.getSize().width,
                        height: image.getSize().height,
                    });
                } catch (e: any) {
                    sendResult({ error: e.message });
                }
                break;
            }
            case "click": {
                if (!browserView) { sendResult({ error: "BrowserView not open" }); break; }
                const sel = (params?.selector || "body").replace(/'/g, "\'");
                const js = "(function(){var el=document.querySelector('" + sel + "');if(!el)return null;var r=el.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2};})()";
                try {
                    const posCode = await browserView.webContents.executeJavaScript(js);
                    const pos = JSON.parse(JSON.stringify(posCode));
                    if (!pos) { sendResult({ error: "Element not found: " + params?.selector }); break; }
                    await browserView.webContents.executeJavaScript(
                        "document.querySelector('" + sel + "').click()"
                    );
                    sendResult({ success: true, clicked: params?.selector, position: pos });
                } catch (e: any) {
                    sendResult({ error: e.message });
                }
                break;
            }
            case "type": {
                if (!browserView) { sendResult({ error: "BrowserView not open" }); break; }
                const tsel = (params?.selector || "input").replace(/'/g, "\'");
                const ttext = params?.text || "";
                try {
                    await browserView.webContents.executeJavaScript(
                        "(function(){var el=document.querySelector('" + tsel + "');if(el){el.focus();el.value='" +
                        ttext.replace(/'/g, "\'") +
                        "';el.dispatchEvent(new Event('input',{bubbles:true}));}})()"
                    );
                    sendResult({ success: true, typed: ttext.substring(0, 50) });
                } catch (e: any) {
                    sendResult({ error: e.message });
                }
                break;
            }
            case "get_html": {
                if (!browserView) { sendResult({ error: "BrowserView not open" }); break; }
                try {
                    const html = await browserView.webContents.executeJavaScript(
                        "document.documentElement.outerHTML"
                    );
                    sendResult({ html: String(html).substring(0, 50000) });
                } catch (e: any) {
                    sendResult({ error: e.message });
                }
                break;
            }
            case "evaluate": {
                if (!browserView) { sendResult({ error: "BrowserView not open" }); break; }
                try {
                    const result = await browserView.webContents.executeJavaScript(params?.js || "null");
                    sendResult({ result: JSON.parse(JSON.stringify(result)) });
                } catch (e: any) {
                    sendResult({ error: e.message });
                }
                break;
            }
            case "get_state": {
                sendResult({
                    url: browserView?.webContents.getURL() || "",
                    title: browserView?.webContents.getTitle() || "",
                    visible: browserViewVisible,
                });
                break;
            }
            default: {
                sendResult({ error: "Unknown method: " + method });
                break;
            }
        }
    } catch (e: any) {
        sendResult({ error: "Handler error: " + e.message });
    }
}

// Start Python backend on app launch
function startBackend() {
    const cwd = path.resolve(__dirname, "..", "..", "..");
    console.log("[Aurora] Starting backend in", cwd);
    try {
        backendProcess = spawn("python", ["main.py"], {
            cwd,
            stdio: "pipe",
            env: { ...process.env, PYTHONUNBUFFERED: "1" },
        });
        backendProcess.stdout?.on("data", (d: Buffer) => {
            console.log("[Aurora backend]", d.toString().trim());
        });
        backendProcess.stderr?.on("data", (d: Buffer) => {
            console.log("[Aurora backend]", d.toString().trim());
        });
    } catch (e: any) {
        console.error("[Aurora] Backend error:", e.message);
    }
}

async function handleBrowserNative(msg: { id: string; method: string; params: any }): Promise<any> {
    const { method, params } = msg;
    try {
        switch (method) {
            case "open":
            case "navigate": {
                const url = params?.url || "about:blank";
                if (!browserView) {
                    browserView = new BrowserView({
                        webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
                    });
                    mainWindow?.addBrowserView(browserView);
                    const bounds = mainWindow!.getContentBounds();
                    const bvWidth = Math.floor(bounds.width * 0.45);
                    browserView.setBounds({ x: bounds.width - bvWidth, y: 60, width: bvWidth, height: bounds.height - 60 });
                    browserView.setAutoResize({ width: true, height: true, horizontal: true, vertical: true });
                    browserView.webContents.on("did-navigate", (_e, navUrl) => {
                        mainWindow?.webContents.send("browser:navigated", { url: navUrl });
                    });
                }
                browserViewVisible = true;
                browserControlledByAI = true;
                mainWindow?.webContents.send("browser:ai_control", { active: true });
                await browserView.webContents.loadURL(url);
                return { success: true, url };
            }
            case "screenshot": {
                if (!browserView) return { error: "BrowserView not open" };
                const img = await browserView.webContents.capturePage();
                return { data_url: "data:image/png;base64," + img.toPNG().toString("base64") };
            }
            case "click": {
                if (!browserView) return { error: "BrowserView not open" };
                const sel = (params?.selector || "body").replace(/'/g, "\\'");
                const pos = await browserView.webContents.executeJavaScript(
                    "(function(){var el=document.querySelector('" + sel + "');if(!el)return null;el.click();var r=el.getBoundingClientRect();return{x:r.x,y:r.y};})()"
                );
                return pos ? { success: true, clicked: params?.selector, position: pos } : { error: "Element not found" };
            }
            case "type": {
                if (!browserView) return { error: "BrowserView not open" };
                const tsel = (params?.selector || "input").replace(/'/g, "\\'");
                const ttext = (params?.text || "").replace(/'/g, "\\'");
                await browserView.webContents.executeJavaScript(
                    "(function(){var el=document.querySelector('" + tsel + "');if(el){el.focus();el.value='" + ttext + "';el.dispatchEvent(new Event('input',{bubbles:true}));}})()"
                );
                return { success: true, typed: (params?.text || "").substring(0, 50) };
            }
            case "get_html": {
                if (!browserView) return { error: "BrowserView not open" };
                const html = await browserView.webContents.executeJavaScript("document.documentElement.outerHTML");
                return { html: String(html).substring(0, 50000) };
            }
            case "evaluate": {
                if (!browserView) return { error: "BrowserView not open" };
                const result = await browserView.webContents.executeJavaScript(params?.js || "null");
                return { result: JSON.parse(JSON.stringify(result)) };
            }
            case "get_state": {
                return { url: browserView?.webContents.getURL() || "", title: browserView?.webContents.getTitle() || "", visible: browserViewVisible };
            }
            default:
                return { error: "Unknown method: " + method };
        }
    } catch (e: any) {
        return { error: e.message || String(e) };
    }
}

function connectBackend() {

    if (ws) {
        try { ws.close(); } catch (_) {}
    }
    ws = new WebSocket(BACKEND_URL + "/ws/desktop");

    ws.on("open", () => {
        console.log("[Aurora] Backend connected");
        mainWindow?.webContents.send("backend:connected");
    });

    ws.on("message", async (data: WebSocket.Data) => {
        try {
            const msg = JSON.parse(data.toString());
            
            // Browser command from backend
            if (msg.type === "browser_cmd") {
                const result = await handleBrowserNative(msg);
                ws?.send(JSON.stringify({ type: "browser_result", id: msg.id, result }));
                return;
            }

            mainWindow?.webContents.send("agent:message", msg);

            // Show notification on task completion
            if (msg.type === "codex/event/task_complete" || msg.type === "done") {
                showNotification("Aurora", "Task completed.");
            }
            if (msg.type === "codex/event/error") {
                showNotification("Aurora", "An error occurred during task execution.");
            }
        } catch {
            mainWindow?.webContents.send("agent:raw", data.toString());
        }
    });

    ws.on("close", () => {
        console.log("[Aurora] Backend disconnected, reconnecting in 5s...");
        setTimeout(connectBackend, 5000);
    });

    ws.on("error", (err) => {
        console.error("[Aurora] WebSocket error:", err.message);
    });
}

function sendToBackend(data: object) {
    if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
    }
}

// Terminal management
const terminals: Map<string, any> = new Map();

let ptyModule: any = null;
let ptyLoadAttempted = false;

function loadPty() {
    if (ptyLoadAttempted) return ptyModule;
    ptyLoadAttempted = true;
    try {
        ptyModule = require("node-pty");
        console.log("[Aurora] node-pty loaded OK");
    } catch (e: any) {
        console.warn("[Aurora] node-pty unavailable, terminal disabled:", e.message);
        ptyModule = null;
    }
    return ptyModule;
}

function createPty(sessionId: string, cwd: string) {
    const pty = loadPty();
    if (!pty) return false;

    try {
        const shell = process.platform === "win32" ? "powershell.exe" : "bash";
        const ptyProcess = pty.spawn(shell, [], {
            name: "xterm-color",
            cols: 120,
            rows: 30,
            cwd: cwd || process.cwd(),
            env: process.env,
        });

        ptyProcess.onData((data: string) => {
            mainWindow?.webContents.send("terminal:data", { sessionId, data });
        });

        ptyProcess.onExit(({ exitCode }: { exitCode: number }) => {
            mainWindow?.webContents.send("terminal:exit", { sessionId, exitCode });
            terminals.delete(sessionId);
        });

        terminals.set(sessionId, ptyProcess);
        return true;
    } catch (e) {
        console.error("Failed to create PTY:", e);
        return false;
    }
}

// IPC Handlers

// Notification from renderer
ipcMain.handle("notification:show", async (_event, { title, body }: { title: string; body: string }) => {
    showNotification(title, body);
});

// Tray settings
ipcMain.handle("tray:setMinimizeToTray", async (_event, enabled: boolean) => {
    minimizeToTray = enabled;
});

ipcMain.handle("tray:getMinimizeToTray", async () => {
    return minimizeToTray;
});

// Search handler
ipcMain.handle("search:inFiles", async (_event, params: { query: string; workspace: string; caseSensitive?: boolean; wholeWord?: boolean; regex?: boolean }) => {
    const { query, workspace, caseSensitive, wholeWord, regex } = params;
    const results: string[] = [];
    const MAX_RESULTS = 100;
    try {
        let pattern: RegExp;
        if (regex) {
            pattern = new RegExp(query, caseSensitive ? "g" : "gi");
        } else {
            const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
            const boundary = wholeWord ? "\\b" : "";
            pattern = new RegExp(boundary + escaped + boundary, caseSensitive ? "g" : "gi");
        }

        function walkDir(dir: string, depth: number) {
            if (depth > 10 || results.length >= MAX_RESULTS) return;
            try {
                const entries = fs.readdirSync(dir, { withFileTypes: true });
                for (const entry of entries) {
                    if (results.length >= MAX_RESULTS) break;
                    const full = path.join(dir, entry.name);
                    if (entry.isDirectory()) {
                        if ([".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".next"].includes(entry.name)) continue;
                        walkDir(full, depth + 1);
                    } else if (entry.isFile()) {
                        const ext = path.extname(entry.name).toLowerCase();
                        const searchable = [".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".go", ".rs", ".java", ".md", ".yaml", ".yml", ".html", ".css", ".sql"].includes(ext);
                        if (!searchable) continue;
                        try {
                            const content = fs.readFileSync(full, "utf-8");
                            const lines = content.split("\n");
                            for (let i = 0; i < lines.length; i++) {
                                if (pattern.test(lines[i])) {
                                    const rel = path.relative(workspace, full).replace(/\\/g, "/");
                                    results.push(`${rel}:${i + 1}: ${lines[i].trim().substring(0, 200)}`);
                                    if (results.length >= MAX_RESULTS) break;
                                }
                            }
                        } catch (_) {}
                    }
                }
            } catch (_) {}
        }
        walkDir(workspace, 0);
    } catch (_) {}
    return results;
});

ipcMain.handle("agent:chat", async (_event, { message, workspace, sessionId, sandboxMode, model, history }) => {
    sendToBackend({ type: "chat", message, workspace, sessionId, sandboxMode, model, history });
    return { sent: true };
});

ipcMain.handle("agent:cancel", async (_event, { sessionId }) => {
    sendToBackend({ type: "cancel", sessionId });
    return { cancelled: true };
});

ipcMain.handle("agent:threadControl", async (_event, data) => {
    const allowedActions = new Set(["steer", "interrupt", "compact", "settings", "followups"]);
    if (!data || !allowedActions.has(data.action)) {
        return { sent: false, error: "Unsupported thread control action" };
    }
    sendToBackend({ ...data, type: "thread_control" });
    return { sent: true };
});

ipcMain.handle("agent:approvalDecision", async (_event, data) => {
    if (!data || !["approve", "deny"].includes(data.action)) {
        return { sent: false, error: "Unsupported approval action" };
    }
    if (ws?.readyState !== WebSocket.OPEN) {
        return { sent: false, error: "Backend disconnected" };
    }
    sendToBackend({ ...data, type: "approval_decision" });
    return { sent: true };
});

ipcMain.handle("sharedObjects:snapshot", async () => {
    try {
        const response = await fetch("http://127.0.0.1:9876/shared-objects");
        if (!response.ok) return { error: `HTTP ${response.status}` };
        return await response.json();
    } catch (e: any) {
        return { error: e.message || String(e) };
    }
});

ipcMain.handle("sharedObjects:set", async (_event, data) => {
    if (!data?.key) return { error: "key is required" };
    try {
        const response = await fetch(`http://127.0.0.1:9876/shared-objects/${encodeURIComponent(data.key)}`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ value: data.value, source: data.source || "desktop" }),
        });
        if (!response.ok) return { error: `HTTP ${response.status}` };
        return await response.json();
    } catch (e: any) {
        return { error: e.message || String(e) };
    }
});

ipcMain.handle("terminal:create", async (_event, { sessionId, cwd }) => {
    return createPty(sessionId, cwd);
});

ipcMain.handle("terminal:write", async (_event, { sessionId, data }) => {
    const pty = terminals.get(sessionId);
    if (pty) { pty.write(data); }
    return { written: true };
});

ipcMain.handle("terminal:resize", async (_event, { sessionId, cols, rows }) => {
    const pty = terminals.get(sessionId);
    if (pty) { pty.resize(cols, rows); }
    return { resized: true };
});

ipcMain.handle("terminal:kill", async (_event, { sessionId }) => {
    const pty = terminals.get(sessionId);
    if (pty) { pty.kill(); terminals.delete(sessionId); }
    return { killed: true };
});

ipcMain.handle("dialog:openFolder", async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
        properties: ["openDirectory"],
    });
    return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:openFile", async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
        properties: ["openFile"],
    });
    return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("file:read", async (_event, filePath: string) => {
    try {
        if (!path.isAbsolute(filePath)) {
            return { error: "Only absolute paths are allowed for file read" };
        }
        return fs.readFileSync(filePath, "utf-8");
    } catch (e: any) {
        return { error: e.message };
    }
});

ipcMain.handle("file:write", async (_event, { filePath, content }: { filePath: string; content: string }) => {
    try {
        // Require absolute path; reject relative paths
        if (!path.isAbsolute(filePath)) {
            return { error: "Only absolute paths are allowed for file write" };
        }
        fs.writeFileSync(filePath, content, "utf-8");
        return { success: true };
    } catch (e: any) {
        return { error: e.message };
    }
});

ipcMain.handle("file:list", async (_event, dirPath: string) => {
    try {
        const entries = fs.readdirSync(dirPath, { withFileTypes: true });
        return entries.map((e) => ({
            name: e.name,
            isDirectory: e.isDirectory(),
            isFile: e.isFile(),
        }));
    } catch (e: any) {
        return { error: e.message };
    }
});

ipcMain.handle("shell:openExternal", async (_event, url: string) => {
    await shell.openExternal(url);
});

// Browser View handlers
// Native browser control via Electron APIs (executeJavaScript + capturePage)

ipcMain.handle("browser:open", async (_event, url: string) => {
    if (!mainWindow) return { error: "No main window" };
    if (!browserView) {
        browserView = new BrowserView({
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true,
                sandbox: true,
            },
        });
        mainWindow.addBrowserView(browserView);
        const bounds = mainWindow.getContentBounds();
        // Position on right side, 45% width
        const bvWidth = Math.floor(bounds.width * 0.45);
        browserView.setBounds({
            x: bounds.width - bvWidth,
            y: 60,
            width: bvWidth,
            height: bounds.height - 60,
        });
        browserView.setAutoResize({ width: true, height: true, horizontal: true, vertical: true });

        browserView.webContents.on("did-navigate", (_e, navUrl) => {
            mainWindow?.webContents.send("browser:navigated", { url: navUrl });
        });
        browserView.webContents.on("did-navigate-in-page", (_e, navUrl) => {
            mainWindow?.webContents.send("browser:navigated", { url: navUrl });
        });
    }
    browserViewVisible = true;
    await browserView.webContents.loadURL(url);
    mainWindow.webContents.send("browser:state", { visible: true, url });
    return { success: true };
});

ipcMain.handle("browser:close", async () => {
    if (browserView && mainWindow) {
        mainWindow.removeBrowserView(browserView);
        (browserView.webContents as any).destroy?.();
        browserView = null;
    }
    browserViewVisible = false;
    browserControlledByAI = false;
    mainWindow?.webContents.send("browser:state", { visible: false });
    mainWindow?.webContents.send("browser:ai_control", { active: false });
    mainWindow?.webContents.send("browser:ai_control", { active: false });
    browserControlledByAI = false;
    return { success: true };
});

ipcMain.handle("browser:navigate", async (_event, url: string) => {
    if (browserView) {
        await browserView.webContents.loadURL(url);
        return { success: true };
    }
    return { error: "No browser view" };
});

ipcMain.handle("browser:back", async () => {
    if (browserView && browserView.webContents.canGoBack()) {
        browserView.webContents.goBack();
        return { success: true };
    }
    return { error: "Cannot go back" };
});

ipcMain.handle("browser:forward", async () => {
    if (browserView && browserView.webContents.canGoForward()) {
        browserView.webContents.goForward();
        return { success: true };
    }
    return { error: "Cannot go forward" };
});

ipcMain.handle("browser:reload", async () => {
    if (browserView) {
        browserView.webContents.reload();
        return { success: true };
    }
    return { error: "No browser view" };
});

ipcMain.handle("browser:getState", async () => {
    const bv = browserView;
    const url = bv ? bv.webContents.getURL() : "";
    return { visible: browserViewVisible, url };
});

// App lifecycle
app.whenReady().then(async () => {
    startBackend();
    await new Promise(r => setTimeout(r, 2500));
    createWindow();
    createTray();
    connectBackend();

    app.on("activate", () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        } else if (mainWindow) {
            mainWindow.show();
        }
    });
});

app.on("before-quit", () => {
    isQuitting = true;
    if (backendProcess) { try { backendProcess.kill(); } catch (_) {} }
});

app.on("window-all-closed", () => {
    ws?.close();
    terminals.forEach((pty) => { try { pty.kill(); } catch (_) {} });
    if (process.platform !== "darwin") app.quit();
});
