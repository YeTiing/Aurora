import React from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";

interface ToolbarProps {
    onToggleMemory?: () => void;
    showMemory?: boolean;
    onToggleAdmin?: () => void;
    showAdmin?: boolean;
    onToggleGoal?: () => void;
    showGoal?: boolean;
    onToggleSkins?: () => void;
    showSkins?: boolean;
    onToggleRe?: () => void;
    showRe?: boolean;
    onToggleDetective?: () => void;
    showDetective?: boolean;
}

export function Toolbar({
    onToggleMemory, showMemory,
    onToggleAdmin, showAdmin,
    onToggleGoal, showGoal,
    onToggleSkins, showSkins,
    onToggleRe, showRe,
    onToggleDetective, showDetective
}: ToolbarProps) {
    const colors = useStore((s) => s.themeColors);
    const createSession = useStore((s) => s.createSession);
    const showRightPanel = useStore((s) => s.showRightPanel);
    const toggleRightPanel = useStore((s) => s.toggleRightPanel);
    const toggleSettings = useStore((s) => s.toggleSettings);

    return (
        <div className="aurora-toolbar">
            <div className="aurora-toolbar-left">
                <span className="toolbar-brand" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <img src="/logo.png" alt="Aurora Logo" className="toolbar-logo" />
                    Aurora
                </span>
            </div>
            <div className="aurora-toolbar-right">
                {onToggleMemory && (
                    <button className={`toolbar-btn ${showMemory ? "active" : ""}`}
                        onClick={onToggleMemory} title="Memory Dashboard (SOUL / Memory / Cron)">
                        🧠
                    </button>
                )}
                {onToggleGoal && (
                    <button className={`toolbar-btn ${showGoal ? "active" : ""}`}
                        onClick={onToggleGoal} title="Goal Tracker">
                        🎯
                    </button>
                )}
                {onToggleAdmin && (
                    <button className={`toolbar-btn ${showAdmin ? "active" : ""}`}
                        onClick={onToggleAdmin} title="Admin Panel (Plugins / MCP / Browser)">
                        🔧
                    </button>
                )}
                {onToggleDetective && (
                    <button className={`toolbar-btn ${showDetective ? "active" : ""}`}
                        onClick={onToggleDetective} title={t("diffDetective")}>
                        🕵
                    </button>
                )}
                {onToggleRe && (
                    <button className={`toolbar-btn ${showRe ? "active" : ""}`}
                        onClick={onToggleRe} title="RE Workspace">
                        🔍
                    </button>
                )}
                {onToggleSkins && (
                    <button className={`toolbar-btn ${showSkins ? "active" : ""}`}
                        onClick={onToggleSkins} title="Theme Skins">
                        🎨
                    </button>
                )}
                <button className={`toolbar-btn ${showRightPanel ? "active" : ""}`}
                    onClick={toggleRightPanel} title="📁">
                    📁
                </button>
                <button className="toolbar-btn" onClick={toggleSettings} title={t("settings")}>
                    ⚙️
                </button>
            </div>
        </div>
    );
}
