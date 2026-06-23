// Codex-style Sidebar — Tab切换：文件 / 对话 / 搜索 · 全中文
import React, { useEffect, useState } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";
import type { FileEntry } from "../../../shared/types";

type SidebarTab = "explorer" | "sessions" | "search";
type TreeNode = { name: string; path: string; isDir: boolean; children: TreeNode[] | null | undefined; loaded: boolean };

const FILE_ICONS: Record<string, string> = {
    ts: "🟦", tsx: "⚛️", js: "🟨", jsx: "⚛️", py: "🐍", rs: "🦀",
    go: "🔷", java: "☕", json: "📋", yaml: "⚙️", yml: "⚙️", toml: "⚙️",
    md: "📝", css: "🎨", html: "🌐", svg: "🖼️", sql: "🗄️", sh: "⚡",
    gitignore: "🙈", dockerfile: "🐳", lock: "🔒",
};

function fileIcon(name: string, isDir: boolean): string {
    if (isDir) return "📁";
    return FILE_ICONS[name.split(".").pop()?.toLowerCase() || ""] || "📄";
}

export function FileTree() {
    const colors = useStore((s) => s.themeColors);
    const workspace = useStore((s) => s.workspace);
    const setWorkspace = useStore((s) => s.setWorkspace);
    const openFile = useStore((s) => s.openFile);
    const activeFile = useStore((s) => s.activeFile);
    const sessions = useStore((s) => s.sessions);
    const activeSessionId = useStore((s) => s.activeSessionId);
    const setActiveSession = useStore((s) => s.setActiveSession);
    const createSession = useStore((s) => s.createSession);

    const [activeTab, setActiveTab] = useState<SidebarTab>("explorer");
    const [rootTree, setRootTree] = useState<TreeNode[]>([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!workspace) return;
        setLoading(true);
        loadChildren(workspace)
            .then((nodes) => { setRootTree(nodes); setLoading(false); })
            .catch(() => setLoading(false));
    }, [workspace]);

    async function loadChildren(dirPath: string): Promise<TreeNode[]> {
        try {
            const result = await window.aurora?.file.list(dirPath);
            if (!result || !Array.isArray(result)) return [];
            return result
                .filter((e: FileEntry) => !e.name.startsWith(".") || e.name === ".env" || e.name === ".gitignore")
                .sort((a: FileEntry, b: FileEntry) => {
                    if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
                    return a.name.localeCompare(b.name);
                })
                .map((e: FileEntry) => ({
                    name: e.name, path: dirPath.replace(/\\/g, "/") + "/" + e.name,
                    isDir: e.isDirectory, children: e.isDirectory ? null : undefined, loaded: false,
                }));
        } catch { return []; }
    }

    async function toggleExpand(node: TreeNode) {
        if (!node.isDir) { openFile(node.path); return; }
        if (node.children === null) {
            node.children = await loadChildren(node.path);
            node.loaded = true;
        }
        setRootTree([...rootTree]);
    }

    function handleOpenFolder() {
        window.aurora?.dialog.openFolder().then((folder: string | null) => {
            if (folder) setWorkspace(folder);
        });
    }

    const tabStyle = (tab: SidebarTab): React.CSSProperties => ({
        flex: 1, textAlign: "center" as const, padding: "6px 0", fontSize: 11,
        fontWeight: activeTab === tab ? 600 : 400,
        color: activeTab === tab ? colors.text : colors.textSecondary,
        borderBottom: activeTab === tab ? `2px solid ${colors.accent}` : "2px solid transparent",
        cursor: "pointer", transition: "color 0.15s, border-color 0.15s",
    });

    const wsName = workspace?.split(/[/\\]/).pop() || "—";

    return (
        <div style={{ height: "100%", display: "flex", flexDirection: "column", fontSize: 13 }}>
            {/* Tab bar */}
            <div style={{ display: "flex", borderBottom: `1px solid ${colors.border}`, flexShrink: 0 }}>
                <div onClick={() => setActiveTab("explorer")} style={tabStyle("explorer")}>{t("files")}</div>
                <div onClick={() => setActiveTab("sessions")} style={tabStyle("sessions")}>{t("chats")}</div>
                <div onClick={() => setActiveTab("search")} style={tabStyle("search")}>{t("search")}</div>
            </div>

            {/* Explorer */}
            {activeTab === "explorer" && (
                <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
                    <div style={{
                        padding: "6px 12px", fontSize: 11, fontWeight: 600,
                        color: colors.textSecondary, display: "flex",
                        justifyContent: "space-between", alignItems: "center",
                        borderBottom: `1px solid ${colors.border}`,
                    }}>
                        <span style={{ textTransform: "uppercase", letterSpacing: 0.5 }}>📂 {wsName}</span>
                        <button onClick={handleOpenFolder} style={{
                            background: "none", border: "none", cursor: "pointer",
                            color: colors.textSecondary, fontSize: 12, padding: "0 2px",
                        }} title={t("openFolder")}>↻</button>
                    </div>
                    <div style={{ flex: 1, overflow: "auto", padding: "2px 0" }}>
                        {loading ? (
                            <div style={{ padding: 12, color: colors.textSecondary, fontSize: 12 }}>{t("loading")}</div>
                        ) : rootTree.length === 0 ? (
                            <div style={{ padding: 12, color: colors.textSecondary, fontSize: 12, fontStyle: "italic" }}>
                                {t("noFiles")}
                            </div>
                        ) : (
                            rootTree.map((node) => (
                                <TreeNodeRow key={node.path} node={node} depth={0}
                                    activeFile={activeFile} colors={colors} onToggle={toggleExpand} />
                            ))
                        )}
                    </div>
                </div>
            )}

            {/* 历史对话记录 */}
            {activeTab === "sessions" && (
                <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
                    <div style={{
                        padding: "6px 12px", display: "flex", justifyContent: "space-between",
                        alignItems: "center", borderBottom: `1px solid ${colors.border}`,
                    }}>
                        <span style={{ fontSize: 11, fontWeight: 600, color: colors.textSecondary, textTransform: "uppercase" }}>
                            {t("sessions")}
                        </span>
                        <button onClick={() => createSession()} style={{
                            background: "none", border: `1px solid ${colors.accent}`,
                            color: colors.accent, cursor: "pointer", fontSize: 16,
                            borderRadius: 4, padding: "0 6px", lineHeight: 1.4,
                        }}>+</button>
                    </div>
                    {sessions.length === 0 ? (
                        <div style={{ padding: 16, color: colors.textSecondary, fontSize: 12, textAlign: "center" }}>
                            {t("noMessages")}
                        </div>
                    ) : (
                        sessions.map((s: any) => {
                            const isActive = s.id === activeSessionId;
                            return (
                                <div key={s.id} onClick={() => setActiveSession(s.id)}
                                    style={{
                                        padding: "8px 12px", cursor: "pointer",
                                        borderLeft: isActive ? `3px solid ${colors.accent}` : "3px solid transparent",
                                        backgroundColor: isActive ? "rgba(88,166,255,0.08)" : "transparent",
                                        borderBottom: `1px solid ${colors.border}`,
                                        transition: "background-color 0.1s",
                                    }}>
                                    <div style={{ fontSize: 12, fontWeight: isActive ? 600 : 400, color: colors.text,
                                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                        {s.title || "未命名"}
                                    </div>
                                    <div style={{ fontSize: 10, color: colors.textSecondary, marginTop: 2, display: "flex", gap: 8 }}>
                                        <span>{(s.messages?.length ?? 0)} 条消息</span>
                                        <span>{new Date(s.updatedAt).toLocaleDateString("zh-CN")}</span>
                                    </div>
                                </div>
                            );
                        })
                    )}
                </div>
            )}

            {/* Search */}
            {activeTab === "search" && (
                <div style={{ padding: 16, textAlign: "center" }}>
                    <button
                        onClick={() => useStore.getState().toggleSearch()}
                        style={{
                            padding: "6px 20px", fontSize: 12, fontWeight: 600,
                            backgroundColor: colors.accent, color: "#fff",
                            border: "none", borderRadius: 6, cursor: "pointer",
                        }}>
                        🔍 {t("searchFiles")}
                    </button>
                </div>
            )}
        </div>
    );
}

function TreeNodeRow({ node, depth, activeFile, colors, onToggle }: {
    node: TreeNode; depth: number; activeFile: string | null;
    colors: any; onToggle: (n: TreeNode) => void;
}) {
    const [expanded, setExpanded] = useState(false);
    const isActive = activeFile?.replace(/\\/g, "/") === node.path;
    const indent = 12 + depth * 14;

    const handleClick = async () => {
        if (node.isDir) {
            if (!node.loaded) await onToggle(node);
            setExpanded(!expanded);
        } else {
            onToggle(node);
        }
    };

    return (
        <>
            <div onClick={handleClick}
                style={{
                    display: "flex", alignItems: "center", gap: 4,
                    padding: `3px 8px 3px ${indent}px`, cursor: "pointer",
                    backgroundColor: isActive ? "rgba(88,166,255,0.12)" : "transparent",
                    color: isActive ? colors.text : colors.textSecondary,
                    fontSize: 12, lineHeight: "20px",
                    borderLeft: isActive ? `2px solid ${colors.accent}` : "2px solid transparent",
                    transition: "background-color 0.1s",
                }}
                onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.backgroundColor = "rgba(255,255,255,0.03)"; }}
                onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.backgroundColor = "transparent"; }}>
                {node.isDir ? (
                    <span style={{ width: 14, textAlign: "center", fontSize: 10,
                        transform: expanded ? "rotate(90deg)" : "none",
                        transition: "transform 0.12s", display: "inline-block" }}>›</span>
                ) : <span style={{ width: 14 }} />}
                <span style={{ fontSize: 13, opacity: 0.8, flexShrink: 0 }}>
                    {node.isDir ? (expanded ? "📂" : "📁") : fileIcon(node.name, false)}
                </span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: isActive ? 600 : 400 }}>
                    {node.name}
                </span>
            </div>
            {node.isDir && expanded && node.children && node.children.map((child) => (
                <TreeNodeRow key={child.path} node={child} depth={depth + 1}
                    activeFile={activeFile} colors={colors} onToggle={onToggle} />
            ))}
        </>
    );
}