import React from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";

interface ToolbarProps {
    onToggleMemory?: () => void;
    showMemory?: boolean;
    onToggleSkins?: () => void;
    showSkins?: boolean;
    onToggleRe?: () => void;
    showRe?: boolean;
    onToggleDetective?: () => void;
    showDetective?: boolean;
}

export function Toolbar({ onToggleMemory, showMemory, onToggleSkins, showSkins, onToggleRe, showRe, onToggleDetective, showDetective, onToggleDocGhost, showDocGhost }: ToolbarProps) {
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
                
                {onToggleDetective && (
                    <button className={`toolbar-btn ${showDetective ? "active" : ""}`}
                        onClick={onToggleDetective} title="Diff Detective">
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
                        onClick={onToggleSkins} title="主题皮肤">
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
