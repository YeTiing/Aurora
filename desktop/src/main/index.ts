import { app, BrowserWindow, Tray, Menu, Notification, ipcMain, dialog, shell, nativeImage } from "electron";
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

const BACKEND_URL = "ws://127.0.0.1:9876";

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
        titleBarStyle: "hiddenInset",
        backgroundColor: "#0d1117",
        show: !startMinimized,
        webPreferences: {
            preload: path.join(__dirname, "preload.js"),
            nodeIntegration: false,
            contextIsolation: true,
        },
    });

    if (process.env.NODE_ENV === "development") {
        mainWindow.loadURL("http://localhost:5173");
        mainWindow.webContents.openDevTools({ mode: "detach" });
    } else {
        mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
    }

    // Minimize to tray instead of closing
    mainWindow.on("close", (event) => {
        if (!isQuitting && minimizeToTray) {
            event.preventDefault();
            mainWindow?.hide();
            return;
        }
    });

    mainWindow.on("closed", () => { mainWindow = null; });

    // If minimized flag, don't show initially
    if (startMinimized && mainWindow) {
        mainWindow.hide();
    }
}

// Backend connection
function connectBackend() {
    if (ws) {
        try { ws.close(); } catch (_) {}
    }
    ws = new WebSocket(BACKEND_URL + "/ws/desktop");

    ws.on("open", () => {
        console.log("[Aurora] Backend connected");
        mainWindow?.webContents.send("backend:connected");
    });

    ws.on("message", (data: WebSocket.Data) => {
        try {
            const msg = JSON.parse(data.toString());
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

ipcMain.handle("agent:chat", async (_event, { message, workspace, sessionId, sandboxMode, model }) => {
    sendToBackend({ type: "chat", message, workspace, sessionId, sandboxMode, model });
    return { sent: true };
});

ipcMain.handle("agent:cancel", async (_event, { sessionId }) => {
    sendToBackend({ type: "cancel", sessionId });
    return { cancelled: true };
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
        return fs.readFileSync(filePath, "utf-8");
    } catch (e: any) {
        return { error: e.message };
    }
});

ipcMain.handle("file:write", async (_event, { filePath, content }: { filePath: string; content: string }) => {
    try {
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

// App lifecycle
app.whenReady().then(() => {
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
});

app.on("window-all-closed", () => {
    ws?.close();
    terminals.forEach((pty) => { try { pty.kill(); } catch (_) {} });
    if (process.platform !== "darwin") app.quit();
});
