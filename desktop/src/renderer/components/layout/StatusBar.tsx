import React from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";

export function StatusBar() {
    const backendConnected = useStore((s) => s.backendConnected);
    const workspace = useStore((s) => s.workspace);
        const toggleSettings = useStore((s) => s.toggleSettings);
    const toggleMonitor = useStore((s) => s.toggleMonitor);
    
    const setTerminalOpen = useStore((s) => s.setTerminalOpen);
    const terminalOpen = useStore((s) => s.terminalOpen);
    const colors = useStore((s) => s.themeColors);

    const wsName = workspace?.split(/[/\\]/).pop() || "—";

    return (
        <div className="aurora-statusbar">
            <div className="status-left">
                <span className={`status-dot ${backendConnected ? "online" : "offline"}`} />
                <span>{backendConnected ? t("connected") : t("disconnected")}</span>
            </div>
            <span style={{ fontFamily: "var(--aurora-mono)", fontSize: 11, color: "var(--aurora-text-muted)" }}>
                {wsName}
            </span>
            <div className="status-right">
                <button className="status-btn" onClick={toggleMonitor} title={t("monitor")}
                    style={useStore.getState().show任务监听器 ? { color: "var(--aurora-accent)", background: "var(--aurora-accent-subtle)" } : {}}>
                    📊
                </button>
                <button className={`status-btn ${terminalOpen ? "active" : ""}`}
                    onClick={() => setTerminalOpen(!terminalOpen)} title="▢ Terminal">
                    ▢
                </button>
                <button className="status-btn" onClick={toggleSettings} title={t("settings")}>
                    ⚙
                </button>
            </div>
        </div>
    );
}
