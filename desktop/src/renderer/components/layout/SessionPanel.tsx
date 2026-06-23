import React, { useState, useMemo, useRef, useEffect } from "react";
import { useStore } from "../../store";
import { useTheme } from "../../hooks";
import type { Session } from "../../../shared/types";

interface ContextMenuState {
    open: boolean;
    x: number;
    y: number;
    sessionId: string;
}

export const SessionPanel: React.FC = () => {
    const colors = useTheme();
    const sessions = useStore((s) => s.sessions);
    const activeSessionId = useStore((s) => s.activeSessionId);
    const setActiveSession = useStore((s) => s.setActiveSession);
    const createSession = useStore((s) => s.createSession);
    const deleteSession = useStore((s) => s.deleteSession);
    const renameSession = useStore((s) => s.renameSession);
    const duplicateSession = useStore((s) => s.duplicateSession);
    const togglePinSession = useStore((s) => s.togglePinSession);
    const toggleArchiveSession = useStore((s) => s.toggleArchiveSession);
    const [search, setSearch] = useState("");
    const [contextMenu, setContextMenu] = useState<ContextMenuState>({ open: false, x: 0, y: 0, sessionId: "" });
    const [renaming, setRenaming] = useState<string | null>(null);
    const [renameValue, setRenameValue] = useState("");
    const renameInputRef = useRef<HTMLInputElement>(null);

    // Close context menu on any click
    useEffect(() => {
        const handler = () => setContextMenu((c) => ({ ...c, open: false }));
        document.addEventListener("click", handler);
        return () => document.removeEventListener("click", handler);
    }, []);

    useEffect(() => {
        if (renaming) renameInputRef.current?.focus();
    }, [renaming]);

    // Sort: pinned first, then by updatedAt
    const sorted = useMemo(() => {
        const arr = [...sessions];
        arr.sort((a, b) => {
            if (a.pinned && !b.pinned) return -1;
            if (!a.pinned && b.pinned) return 1;
            return b.updatedAt - a.updatedAt;
        });
        return arr;
    }, [sessions]);

    const filtered = useMemo(() => {
        if (!search.trim()) return sorted;
        const q = search.toLowerCase();
        return sorted.filter(
            (s) =>
                s.title.toLowerCase().includes(q) ||
                s.workspace.toLowerCase().includes(q)
        );
    }, [sorted, search]);

    const handleContextMenu = (e: React.MouseEvent, sessionId: string) => {
        e.preventDefault();
        e.stopPropagation();
        setContextMenu({ open: true, x: e.clientX, y: e.clientY, sessionId });
    };

    const handleRename = (id: string, title: string) => {
        setRenaming(id);
        setRenameValue(title || "");
        setContextMenu((c) => ({ ...c, open: false }));
    };

    const submitRename = (id: string) => {
        if (renameValue.trim()) {
            renameSession(id, renameValue.trim());
        }
        setRenaming(null);
    };

    const handleExport = async (id: string) => {
        const s = sessions.find((x: Session) => x.id === id);
        if (!s) return;
        setContextMenu((c: ContextMenuState) => ({ ...c, open: false }));
        try {
            const r = await fetch("http://127.0.0.1:9876/sessions/export", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ format: "md", session: s }),
            });
            const data = await r.json();
            const blob = new Blob([data.content], { type: "text/markdown" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            const slug = (s.title || "session").replace(/[^a-zA-Z0-9_\u4e00-\u9fff]/g, "_").slice(0, 50);
            a.download = slug + ".md";
            a.click();
        } catch (e: any) { console.warn("Export failed:", e); }
    };

    handleDuplicate = (id: string) => {
        const newId = duplicateSession(id);
        if (newId) setActiveSession(newId);
        setContextMenu((c) => ({ ...c, open: false }));
    };

    const formatDate = (ts: number) => {
        const d = new Date(ts);
        const now = new Date();
        const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
        if (diffDays === 0) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
        if (diffDays === 1) return t("yesterday");
        if (diffDays < 7) return `${diffDays}d ago`;
        return d.toLocaleDateString();
    };

    const messageCount = (s: Session) => s.messages?.length ?? 0;

    const ctxSession = sessions.find((s) => s.id === contextMenu.sessionId);

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", backgroundColor: colors.bg }}>
            <div style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "8px 12px", borderBottom: `1px solid ${colors.border}`,
            }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: colors.text }}>Sessions</span>
                <button
                    onClick={() => createSession()}
                    style={{
                        padding: "2px 10px", fontSize: 18, lineHeight: 1,
                        backgroundColor: "transparent", color: colors.accent,
                        border: `1px solid ${colors.accent}`, borderRadius: 4, cursor: "pointer",
                        fontWeight: 600,
                    }}
                    title=t("newSession")
                >
                    +
                </button>
            </div>

            <div style={{ padding: "6px 8px" }}>
                <input
                    type="text"
                    placeholder=t("searchSessions")
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    style={{
                        width: "100%", padding: "4px 8px", fontSize: 12,
                        backgroundColor: colors.bgSecondary, color: colors.text,
                        border: `1px solid ${colors.border}`, borderRadius: 4,
                        outline: "none", boxSizing: "border-box",
                    }}
                />
            </div>

            <div style={{ flex: 1, overflow: "auto" }}>
                {filtered.length === 0 && (
                    <div style={{ padding: 16, textAlign: "center", color: colors.textSecondary, fontSize: 12 }}>
                        No sessions found
                    </div>
                )}
                {filtered.map((s) => {
                    const isActive = s.id === activeSessionId;
                    return (
                        <div
                            key={s.id}
                            onClick={() => setActiveSession(s.id)}
                            onContextMenu={(e) => handleContextMenu(e, s.id)}
                            style={{
                                padding: "8px 12px", cursor: "pointer",
                                backgroundColor: isActive ? "rgba(88,166,255,0.1)" : "transparent",
                                borderLeft: isActive ? `3px solid ${colors.accent}` : "3px solid transparent",
                                transition: "background-color 0.15s",
                                opacity: s.archived ? 0.45 : 1,
                            }}
                            onMouseEnter={(e) => {
                                if (!isActive) e.currentTarget.style.backgroundColor = "rgba(255,255,255,0.03)";
                            }}
                            onMouseLeave={(e) => {
                                if (!isActive) e.currentTarget.style.backgroundColor = "transparent";
                            }}
                        >
                            {renaming === s.id ? (
                                <input
                                    ref={renameInputRef}
                                    value={renameValue}
                                    onChange={(e) => setRenameValue(e.target.value)}
                                    onBlur={() => submitRename(s.id)}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter") submitRename(s.id);
                                        if (e.key === "Escape") setRenaming(null);
                                    }}
                                    onClick={(e) => e.stopPropagation()}
                                    style={{
                                        width: "100%", padding: "2px 4px", fontSize: 12,
                                        backgroundColor: colors.bgSecondary, color: colors.text,
                                        border: `1px solid ${colors.accent}`, borderRadius: 3,
                                        outline: "none",
                                    }}
                                />
                            ) : (
                                <>
                                    <div style={{
                                        display: "flex", justifyContent: "space-between",
                                        alignItems: "flex-start",
                                    }}>
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{
                                                fontSize: 13, fontWeight: isActive ? 600 : 400,
                                                color: colors.text, whiteSpace: "nowrap",
                                                overflow: "hidden", textOverflow: "ellipsis",
                                                display: "flex", alignItems: "center", gap: 4,
                                            }}>
                                                {s.pinned && <span style={{ fontSize: 10 }}>📌</span>}
                                                {s.archived && <span style={{ fontSize: 10 }}>📦</span>}
                                                {s.title || "Untitled"}
                                            </div>
                                            <div style={{
                                                fontSize: 11, color: colors.textSecondary,
                                                marginTop: 2, whiteSpace: "nowrap",
                                                overflow: "hidden", textOverflow: "ellipsis",
                                            }}>
                                                {s.workspace || "~/"}
                                            </div>
                                            <div style={{
                                                fontSize: 10, color: colors.textSecondary,
                                                marginTop: 2, display: "flex", gap: 8,
                                            }}>
                                                <span>{messageCount(s)} msgs</span>
                                                <span>{formatDate(s.updatedAt)}</span>
                                            </div>
                                        </div>
                                    </div>
                                </>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Context Menu */}
            {contextMenu.open && ctxSession && (
                <div
                    className="session-context-menu"
                    style={{
                        position: "fixed",
                        left: contextMenu.x,
                        top: contextMenu.y,
                        zIndex: 200,
                    }}
                    onClick={(e) => e.stopPropagation()}
                >
                    <button className="ctx-menu-item" onClick={() => handleRename(ctxSession.id, ctxSession.title)}>
                        <span className="ctx-icon">✏️</span> Rename
                    </button>
                    <button className="ctx-menu-item" onClick={() => handleDuplicate(ctxSession.id)}>
                        <span className="ctx-icon">📋</span> Duplicate
                    </button>
                    <button className="ctx-menu-item" onClick={() => { togglePinSession(ctxSession.id); setContextMenu((c) => ({ ...c, open: false })); }}>
                        <span className="ctx-icon">📌</span> {ctxSession.pinned ? t("unpin") : t("pin")}
                    </button>
                    <button className="ctx-menu-item" onClick={() => { toggleArchiveSession(ctxSession.id); setContextMenu((c) => ({ ...c, open: false })); }}>
                        <span className="ctx-icon">📦</span> {ctxSession.archived ? "Unarchive" : t("archive")}
                    </button>
                    <button className="ctx-menu-item" onClick={() => handleExport(ctxSession.id)}>
                        <span className="ctx-icon">馃搫</span> Export
                    </button>
                    <div className="ctx-divider" />
                    <button
                        className="ctx-menu-item ctx-danger"
                        onClick={() => { deleteSession(ctxSession.id); setContextMenu((c) => ({ ...c, open: false })); }}
                    >
                        <span className="ctx-icon">🗑️</span> Delete
                    </button>
                </div>
            )}
        </div>
    );
};
