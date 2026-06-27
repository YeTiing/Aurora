import React, { useState } from "react";
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
    onToggleBrowser?: () => void;
    showBrowser?: boolean;
    onToggleSocial?: () => void;
    showSocial?: boolean;
}

export function Toolbar({
    onToggleMemory, showMemory,
    onToggleAdmin, showAdmin,
    onToggleGoal, showGoal,
    onToggleSkins, showSkins,
    onToggleRe, showRe,
    onToggleDetective, showDetective,
    onToggleBrowser, showBrowser,
    onToggleSocial, showSocial,
}: ToolbarProps) {
    const [toolsOpen, setToolsOpen] = useState(false);
    const createSession = useStore((s) => s.createSession);
    const showRightPanel = useStore((s) => s.showRightPanel);
    const toggleRightPanel = useStore((s) => s.toggleRightPanel);
    const toggleSettings = useStore((s) => s.toggleSettings);
    const backendConnected = useStore((s) => s.backendConnected);

    const tools = [
        { label: "浏览器", icon: "🌐", active: showBrowser, onClick: onToggleBrowser },
        { label: "资源", icon: "🔗", active: showSocial, onClick: onToggleSocial },
        { label: "记忆", icon: "🧠", active: showMemory, onClick: onToggleMemory },
        { label: "目标", icon: "🎯", active: showGoal, onClick: onToggleGoal },
        { label: "搜索", icon: "🔍", active: showRe, onClick: onToggleRe },
        { label: "侦探", icon: "🕵", active: showDetective, onClick: onToggleDetective },
        { label: "皮肤", icon: "🎨", active: showSkins, onClick: onToggleSkins },
        { label: "管理", icon: "🔧", active: showAdmin, onClick: onToggleAdmin },
    ].filter((item) => item.onClick);

    const runTool = (onClick?: () => void) => {
        onClick?.();
        setToolsOpen(false);
    };

    return (
        <div className="aurora-toolbar">
            <div className="aurora-toolbar-left">
                <span className="toolbar-brand">
                    <img src="/logo.png" alt="Aurora Logo" className="toolbar-logo" />
                    <strong>Aurora</strong>
                </span>
                <span className={`toolbar-status ${backendConnected ? "online" : "offline"}`}>
                    <span />{backendConnected ? "在线" : "离线"}
                </span>
            </div>

            <div className="aurora-toolbar-right">
                <button className="toolbar-action primary" onClick={() => createSession()} title="新建对话">
                    ＋ 新对话
                </button>

                <div className="toolbar-tools">
                    <button className={`toolbar-action ${toolsOpen ? "active" : ""}`} onClick={() => setToolsOpen((v) => !v)}>
                        工具
                    </button>
                    {toolsOpen && (
                        <>
                            <div className="toolbar-tools-backdrop" onClick={() => setToolsOpen(false)} />
                            <div className="toolbar-tools-menu">
                                {tools.map((item) => (
                                    <button key={item.label} className={`toolbar-tool-item ${item.active ? "active" : ""}`} onClick={() => runTool(item.onClick)}>
                                        <span>{item.icon}</span>
                                        {item.label}
                                    </button>
                                ))}
                            </div>
                        </>
                    )}
                </div>

                <button className={`toolbar-action icon ${showRightPanel ? "active" : ""}`}
                    onClick={toggleRightPanel} title="文件">
                    📁
                </button>
                <button className="toolbar-action icon" onClick={toggleSettings} title={t("settings")}>
                    ⚙️
                </button>
            </div>
        </div>
    );
}
