import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useStore } from "../../store";
import { useTheme } from "../../hooks";

interface Command {
    id: string;
    label: string;
    category: string;
    shortcut?: string;
    action: () => void;
}

export const CommandPalette: React.FC<{ onClose: () => void }> = ({ onClose }) => {
    const colors = useTheme();
    const [query, setQuery] = useState("");
    const [selectedIdx, setSelectedIdx] = useState(0);
    const inputRef = useRef<HTMLInputElement>(null);

    const toggleFileTree = useStore((s) => s.toggleFileTree);
    const toggleSettings = useStore((s) => s.toggleSettings);
    const toggleSearch = useStore((s) => s.toggleSearch);
    const toggleTheme = useStore((s) => s.toggleTheme);
    const createSession = useStore((s) => s.createSession);
    const sessions = useStore((s) => s.sessions);
    const setActiveSession = useStore((s) => s.setActiveSession);

    const commands: Command[] = useMemo(() => [
        { id: "new-session", label: "New Session", category: "Sessions", shortcut: "Ctrl+N", action: createSession },
        { id: "toggle-filetree", label: "Toggle File Tree", category: "View", shortcut: "Ctrl+B", action: toggleFileTree },
        { id: "toggle-search", label: "Search in Files", category: "View", shortcut: "Ctrl+Shift+F", action: toggleSearch },
        { id: "toggle-settings", label: "Open Settings", category: "View", shortcut: "Ctrl+,", action: toggleSettings },
        { id: "toggle-theme", label: "Toggle Dark/Light Theme", category: "View", shortcut: "Ctrl+Shift+T", action: toggleTheme },
        ...sessions.slice(0, 10).map((s) => ({
            id: `session-${s.id}`,
            label: s.title || "Untitled",
            category: "Sessions",
            action: () => setActiveSession(s.id),
        })),
    ], [sessions, toggleFileTree, toggleSettings, toggleSearch, toggleTheme, createSession, setActiveSession]);

    const filtered = useMemo(() => {
        if (!query.trim()) return commands;
        const q = query.toLowerCase();
        return commands.filter(
            (c) => c.label.toLowerCase().includes(q) || c.category.toLowerCase().includes(q)
        );
    }, [commands, query]);

    useEffect(() => {
        setSelectedIdx(0);
    }, [filtered.length]);

    useEffect(() => {
        inputRef.current?.focus();
    }, []);

    const executeCommand = useCallback((cmd: Command) => {
        cmd.action();
        onClose();
    }, [onClose]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        switch (e.key) {
            case "Escape":
                e.preventDefault();
                onClose();
                break;
            case "ArrowDown":
                e.preventDefault();
                setSelectedIdx((prev) => Math.min(prev + 1, filtered.length - 1));
                break;
            case "ArrowUp":
                e.preventDefault();
                setSelectedIdx((prev) => Math.max(prev - 1, 0));
                break;
            case "Enter":
                e.preventDefault();
                if (filtered[selectedIdx]) {
                    executeCommand(filtered[selectedIdx]);
                }
                break;
        }
    }, [filtered, selectedIdx, executeCommand, onClose]);

    return (
        <div
            style={{
                position: "fixed", inset: 0, zIndex: 1000,
                display: "flex", justifyContent: "center", paddingTop: "15vh",
                backgroundColor: "rgba(0,0,0,0.5)",
            }}
            onClick={onClose}
        >
            <div
                style={{
                    width: 520, maxHeight: "60vh",
                    backgroundColor: colors.bgSecondary,
                    border: `1px solid ${colors.border}`,
                    borderRadius: 12, overflow: "hidden",
                    boxShadow: "0 16px 48px rgba(0,0,0,0.4)",
                }}
                onClick={(e) => e.stopPropagation()}
            >
                <div style={{
                    padding: "12px 16px", borderBottom: `1px solid ${colors.border}`,
                }}>
                    <input
                        ref={inputRef}
                        type="text"
                        placeholder={t("typeCommand")}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                        style={{
                            width: "100%", padding: "8px 0", fontSize: 15,
                            backgroundColor: "transparent", color: colors.text,
                            border: "none", outline: "none",
                        }}
                    />
                </div>

                <div style={{ maxHeight: "40vh", overflow: "auto", padding: "4px 0" }}>
                    {filtered.length === 0 && (
                        <div style={{ padding: 16, textAlign: "center", color: colors.textSecondary, fontSize: 13 }}>
                            No commands found
                        </div>
                    )}
                    {filtered.map((cmd, idx) => (
                        <div
                            key={cmd.id}
                            onClick={() => executeCommand(cmd)}
                            onMouseEnter={() => setSelectedIdx(idx)}
                            style={{
                                display: "flex", alignItems: "center", justifyContent: "space-between",
                                padding: "8px 16px", cursor: "pointer",
                                backgroundColor: idx === selectedIdx ? "rgba(88,166,255,0.12)" : "transparent",
                                borderLeft: idx === selectedIdx ? `3px solid ${colors.accent}` : "3px solid transparent",
                                transition: "background-color 0.1s",
                            }}
                        >
                            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                <span style={{ fontSize: 16, color: colors.textSecondary, width: 20, textAlign: "center" }}>
                                    {cmd.category === "Sessions" ? "💬" : "⚡"}
                                </span>
                                <div>
                                    <div style={{ fontSize: 13, color: colors.text, fontWeight: idx === selectedIdx ? 600 : 400 }}>
                                        {cmd.label}
                                    </div>
                                    <div style={{ fontSize: 10, color: colors.textSecondary }}>
                                        {cmd.category}
                                    </div>
                                </div>
                            </div>
                            {cmd.shortcut && (
                                <span style={{
                                    fontSize: 11, color: colors.textSecondary,
                                    backgroundColor: "rgba(255,255,255,0.06)",
                                    padding: "2px 6px", borderRadius: 4,
                                    fontFamily: "monospace",
                                }}>
                                    {cmd.shortcut}
                                </span>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

// ── Keyboard Shortcuts Hook ──

export interface ShortcutDef {
    key: string;
    ctrl?: boolean;
    shift?: boolean;
    alt?: boolean;
    action: () => void;
    description: string;
}

export function useKeyboardShortcuts(shortcuts: ShortcutDef[]) {
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            for (const s of shortcuts) {
                const keyMatch = e.key.toLowerCase() === s.key.toLowerCase();
                const ctrlMatch = s.ctrl ? (e.ctrlKey || e.metaKey) : !e.ctrlKey && !e.metaKey;
                const shiftMatch = s.shift ? e.shiftKey : !e.shiftKey;
                const altMatch = s.alt ? e.altKey : !e.altKey;

                if (keyMatch && ctrlMatch && shiftMatch && altMatch) {
                    // Don't fire if we're in an input
                    const tag = (e.target as HTMLElement)?.tagName;
                    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") continue;
                    e.preventDefault();
                    s.action();
                    return;
                }
            }
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, [shortcuts]);
}