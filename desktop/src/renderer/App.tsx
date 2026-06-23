import React, { useEffect, useState, useCallback } from "react";
import { useStore } from "./store";
import { useAgent } from "./hooks";
import { ChatPanel } from "./components/chat/ChatPanel";
import type { FileAttachment } from "./components/chat/ChatPanel";
import { EditorPanel } from "./components/editor/EditorPanel";
import { TerminalPanel } from "./components/terminal/TerminalPanel";
import { FileTree } from "./components/layout/FileTree";
import { StatusBar } from "./components/layout/StatusBar";
import { Toolbar } from "./components/layout/Toolbar";
import { SessionPanel } from "./components/layout/SessionPanel";
import { PlanPanel } from "./components/layout/PlanPanel";
import { MonitorPanel } from "./components/layout/MonitorPanel";
import { SettingsPanel } from "./components/layout/SettingsPanel";
import { CommandPalette } from "./components/layout/CommandPalette";
import { SearchPanel } from "./components/layout/SearchPanel";
import { MemoryDashboard } from "./components/layout/MemoryDashboard";
import { SkinBrowser } from "./components/layout/SkinBrowser";
import { RePanel } from "./components/layout/RePanel";
import { t, setLang } from "./i18n";
import { useGlobalShortcuts, getShortcutManager } from "./shortcuts";
import { useInitializeTheme } from "./theme";
import "./styles.css";

export default function App() {
    const colors = useStore((s) => s.themeColors);
    const { sendMessage, cancelRequest } = useAgent();
    const activeSessionId = useStore((s) => s.activeSessionId);
    const sessions = useStore((s) => s.sessions);
    const loadSessions = useStore((s) => s.loadSessions);
    const showRightPanel = useStore((s) => s.showRightPanel);
    const toggleRightPanel = useStore((s) => s.toggleRightPanel);
    const showSettings = useStore((s) => s.showSettings);
    const toggleSettings = useStore((s) => s.toggleSettings);
    const toggleSearch = useStore((s) => s.toggleSearch);
    const showMonitor = useStore((s) => s.showMonitor);
    const toggleMonitor = useStore((s) => s.toggleMonitor);
    const showSearch = useStore((s) => s.showSearch);
  const [showMemory, setShowMemory] = useState(false);
    const [showSkins, setShowSkins] = useState(false);
    const [showRe, setShowRe] = useState(false);
    const createSession = useStore((s) => s.createSession);
    const setActiveSession = useStore((s) => s.setActiveSession);
    const setTerminalOpen = useStore((s) => s.setTerminalOpen);
    const openFiles = useStore((s) => s.openFiles);
    const activeFile = useStore((s) => s.activeFile);
    const setActiveFile = useStore((s) => s.setActiveFile);
    const closeFile = useStore((s) => s.closeFile);

    const [showCommandPalette, setShowCommandPalette] = useState(false);
    // "chat" = show chat, filepath = show editor for that file
    const [activeCenterTab, setActiveCenterTab] = useState<string>("chat");

    useInitializeTheme();

    useEffect(() => {
        loadSessions();
        setLang("zh-CN");
        useStore.getState().reloadLLMSettings();
    }, [loadSessions]);

    // When a file is opened externally (from file tree), switch to it
    useEffect(() => {
        if (activeFile) {
            setActiveCenterTab(activeFile.replace(/\\/g, "/"));
        }
    }, [activeFile]);

    // Register shortcuts
    useEffect(() => {
        const sm = getShortcutManager();
        sm.register("send", () => {});
        sm.register("commandPalette", () => setShowCommandPalette((prev) => !prev));
        sm.register("toggleSidebar", () => toggleRightPanel());
        sm.register("settings", () => toggleSettings());
        sm.register("newSession", () => createSession());
        sm.register("toggleSearch", () => toggleSearch());
        sm.register("clearChat", () => {
            const sid = useStore.getState().activeSessionId;
            if (sid && window.confirm(t("clearChatConfirm"))) {
                const sessions = useStore.getState().sessions.map((s: any) =>
                    s.id === sid ? { ...s, messages: [] } : s
                );
                useStore.setState({ sessions });
            }
        });
        return () => {
            ["send", "commandPalette", "toggleSidebar", "settings", "newSession", "toggleSearch", "clearChat"].forEach(
                (id) => sm.unregister(id)
            );
        };
    }, [toggleRightPanel, toggleSettings, toggleSearch, createSession]);

    useGlobalShortcuts();

    const handleSendMessage = useCallback(async (msg: string, files?: FileAttachment[]) => {
        let content = msg;
        if (files && files.length > 0) {
            const fileList = files.map((f) => `[File: ${f.name} (${f.mimeType})]${f.path ? " @" + f.path : ""}`).join("\n");
            content = msg ? `${msg}\n\n${fileList}` : fileList;
        }
        await sendMessage(content);
    }, [sendMessage]);

    const activeSession = sessions.find((s: any) => s.id === activeSessionId);
    const plan = activeSession?.plan ?? [];

    const handleCloseFileTab = (filePath: string, e: React.MouseEvent) => {
        e.stopPropagation();
        closeFile(filePath);
        if (activeCenterTab === filePath.replace(/\\/g, "/")) {
            // Switch to chat or next file
            const remaining = openFiles.filter((f: string) => f !== filePath);
            if (remaining.length > 0) {
                const nextFilePath = remaining[remaining.length - 1].replace(/\\/g, "/");
                setActiveCenterTab(nextFilePath);
                setActiveFile(remaining[remaining.length - 1]);
            } else {
                setActiveCenterTab("chat");
                setActiveFile(null);
            }
        }
    };

    return (
        <div className="aurora-app" style={{ backgroundColor: colors.bg, color: colors.text }}>
            <Toolbar
            onToggleMemory={() => setShowMemory(!showMemory)}
            showMemory={showMemory}
            onToggleSkins={() => setShowSkins(!showSkins)}
            showSkins={showSkins}
            onToggleRe={() => setShowRe(!showRe)}
            showRe={showRe} />

            <div className="aurora-main">
                {/* Left: Session list */}
                <div className="aurora-left-sidebar" style={{ borderRight: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary }}>
                    <SessionPanel />
                </div>

                {/* Center: Tabs + Content */}
                <div className="aurora-center">
                    {/* Tab bar */}
                    {openFiles.length > 0 && (
                        <div className="aurora-tabbar">
                            <div className={`tab-item ${activeCenterTab === "chat" ? "active" : ""}`}
                                onClick={() => { setActiveCenterTab("chat"); setActiveFile(null); }}>
                                💬 Chat
                            </div>
                            {openFiles.map((f: string) => {
                                const fname = f.split(/[/\\]/).pop() || f;
                                const normalized = f.replace(/\\/g, "/");
                                const isActive = activeCenterTab === normalized;
                                const ext = fname.split(".").pop()?.toLowerCase() || "";
                                const tabDotColors: Record<string, string> = {
                                    py: "#3572A5", ts: "#3178C6", tsx: "#3178C6", js: "#F7DF1E",
                                    jsx: "#F7DF1E", json: "#5B5B5B", md: "#083FA1", css: "#563D7C",
                                    html: "#E34F26", go: "#00ADD8", rs: "#DEA584", java: "#B07219",
                                };
                                const dotColor = tabDotColors[ext] || colors.textSecondary;
                                return (
                                    <div key={f} className={`tab-item ${isActive ? "active" : ""}`}
                                        onClick={() => { setActiveCenterTab(normalized); setActiveFile(f); }}>
                                        <span style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: dotColor, flexShrink: 0 }} />
                                        {fname}
                                        <span className="tab-close" onClick={(e) => handleCloseFileTab(f, e)}>×</span>
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {/* Content area */}
                    {activeCenterTab === "chat" || openFiles.length === 0 ? (
                        <div className="aurora-chat-wrapper">
                            <ChatPanel onSend={handleSendMessage} onCancel={cancelRequest} />
                        </div>
                    ) : (
                        <EditorPanel />
                    )}
                </div>

                {/* Right: File tree + Plan */}
                <div className={`aurora-right-sidebar ${!showRightPanel ? "collapsed" : ""}`}
                    style={{ borderLeft: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary }}>
                    <div style={{ flex: "1 1 60%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
                        <div className="aurora-right-header" style={{ borderBottom: `1px solid ${colors.border}` }}>
                            <span>📁 {t("files")}</span>
                            <button className="aurora-right-close" onClick={toggleRightPanel} title={t("closePanel")}>✕</button>
                        </div>
                        <div style={{ flex: 1, overflow: "hidden" }}>
                            <FileTree />
                        </div>
                    </div>
                    <div style={{ flex: "0 0 auto", maxHeight: "40%", overflow: "auto", borderTop: `1px solid ${colors.border}` }}>
                        <div className="aurora-right-header" style={{ borderBottom: `1px solid ${colors.border}` }}>
                            <span>📋 {t("plan")}</span>
                        </div>
                        <PlanPanel plan={plan} />
                    </div>
                </div>
            </div>

            <StatusBar />
            <TerminalPanel />

            {showMonitor && <MonitorPanel onClose={toggleMonitor} />}
            {showSettings && <SettingsPanel onClose={toggleSettings} />}
            {showSearch && <SearchPanel />
            {showMemory && <MemoryDashboard />}}
            {showRe && <RePanel onClose={() => setShowRe(false)} />}
            {showSkins && <SkinBrowser onClose={() => setShowSkins(false)} />}
            {showCommandPalette && <CommandPalette onClose={() => setShowCommandPalette(false)} />}
        </div>
    );
}
