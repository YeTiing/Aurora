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
}

export function Toolbar({ onToggleMemory, showMemory, onToggleSkins, showSkins, onToggleRe, showRe }: ToolbarProps) {
    const colors = useStore((s) => s.themeColors);
    const createSession = useStore((s) => s.createSession);
    const showRightPanel = useStore((s) => s.showRightPanel);
    const toggleRightPanel = useStore((s) => s.toggleRightPanel);

    return (
        <div className="aurora-toolbar">
            <div className="aurora-toolbar-left">
                <span className="toolbar-brand">Aurora</span>
            </div>
            <div className="aurora-toolbar-right">
                <button className="toolbar-btn" onClick={() => { createSession(); }} title={t("newChat")}>
                    + {t("newChat")}
                </button>
                {onToggleRe && (
                    <button className={`toolbar-btn ${showRe ? "active" : ""}`}
                        onClick={onToggleRe} title="RE Workspace">
                        🔍
                    </button>
                )}
                {onToggleSkins && (
                    <button className={`toolbar-btn ${showSkins ? "active" : ""}`}
                        onClick={onToggleSkins} title="Skins">
                        🎨
                    </button>
                )}
                <button className={`toolbar-btn ${showRightPanel ? "active" : ""}`}
                    onClick={toggleRightPanel} title="📁">
                    📁
                </button>
            </div>
        </div>
    );
}
