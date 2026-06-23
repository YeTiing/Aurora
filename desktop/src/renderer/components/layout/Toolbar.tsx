import React from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";

export function Toolbar() {
    const colors = useStore((s) => s.themeColors);
    const createSession = useStore((s) => s.createSession);
    const showRightPanel = useStore((s) => s.showRightPanel);
    const toggleRightPanel = useStore((s) => s.toggleRightPanel);

    return (
        <div className="aurora-toolbar">
            <div className="aurora-toolbar-left">
                <span className="toolbar-brand">✦ Aurora</span>
            </div>
            <div className="aurora-toolbar-right">
                <button className="toolbar-btn" onClick={() => { createSession(); }} title={t("newChat")}>
                    ➕ {t("newChat")}
                </button>
                <button className={`toolbar-btn ${showRightPanel ? "active" : ""}`}
                    onClick={toggleRightPanel} title="📁">
                    📁
                </button>
            </div>
        </div>
    );
}
