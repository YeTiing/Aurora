import React, { useState, useCallback } from "react";
import { useStore } from "../../store";

// Electron preload bridge
declare global {
    interface Window {
        auroraAPI?: {
            browser?: {
                open: (url: string) => Promise<any>;
                close: () => Promise<any>;
                navigate: (url: string) => Promise<any>;
                back: () => Promise<any>;
                forward: () => Promise<any>;
                reload: () => Promise<any>;
                getState: () => Promise<{ visible: boolean; url: string }>;
                onState: (cb: (data: any) => void) => void;
                onNavigated: (cb: (data: any) => void) => void;
            };
        };
    }
}

const BOOKMARKS = [
    { name: "bilibili", label: "Bilibili", icon: "▶", url: "https://www.bilibili.com" },
    { name: "douyin", label: "Douyin", icon: "♫", url: "https://www.douyin.com" },
    { name: "github", label: "GitHub", icon: "⬡", url: "https://github.com" },
    { name: "xiaohongshu", label: "Xiaohongshu", icon: "📕", url: "https://www.xiaohongshu.com" },
    { name: "google", label: "Google", icon: "🔍", url: "https://www.google.com" },
    { name: "stackoverflow", label: "StackOverflow", icon: "📚", url: "https://stackoverflow.com" },
    { name: "npm", label: "npm", icon: "📦", url: "https://www.npmjs.com" },
    { name: "pypi", label: "PyPI", icon: "🐍", url: "https://pypi.org" },
];

export function BrowserPanel() {
    const [url, setUrl] = useState("https://www.google.com");
    const [currentUrl, setCurrentUrl] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [aiControlling, setAiControlling] = useState(false);

    const openBrowser = useCallback(async (targetUrl: string) => {
        setLoading(true);
        setError("");
        try {
            if (window.auroraAPI) {
                const result = await window.auroraAPI?.browser?.open(targetUrl);
                if (result.error) {
                    setError(result.error);
                } else {
                    setCurrentUrl(targetUrl);
                }
            } else {
                // Fallback for dev: open in system browser
                window.open(targetUrl, "_blank");
                setCurrentUrl(targetUrl);
            }
        } catch (e: any) {
            setError(e.message || "Failed to open browser");
        } finally {
            setLoading(false);
        }
    }, []);

    const closeBrowser = useCallback(async () => {
        try {
            if (window.auroraAPI) {
                await window.auroraAPI?.browser?.close();
            }
            setCurrentUrl("");
        } catch (_) {}
    }, []);

    const navigate = useCallback(async () => {
        if (!url.trim()) return;
        await openBrowser(url.trim());
        setUrl(url.trim());
    }, [url, openBrowser]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            navigate();
        }
    }, [navigate]);

    React.useEffect(() => {
        if (window.auroraAPI?.browser?.onState) {
            window.auroraAPI?.browser?.onState((data: any) => {
                if (data.url) setCurrentUrl(data.url);
            });
        }
        // Listen for AI control events from Electron main
        const handler = (e: any) => {
            setAiControlling(e.detail?.active || false);
            if (e.detail?.url) setCurrentUrl(e.detail.url);
        };
        window.addEventListener("aurora:aiBrowserControl", handler);
        return () => window.removeEventListener("aurora:aiBrowserControl", handler);
    }, []);

    return (
        <div style={{
            display: "flex", flexDirection: "column", height: "100%",
            background: "var(--bg-panel, #1a1b26)", color: "var(--text, #c0caf5)",
            fontFamily: "var(--font-mono, monospace)", fontSize: 13,
        }}>
            {/* URL Bar */}
            <div style={{
                display: "flex", gap: 6, padding: "8px 10px",
                borderBottom: "1px solid var(--border, #252636)",
                alignItems: "center",
            }}>
                <button onClick={closeBrowser}
                    style={btnStyle} title="Close browser">✕</button>
                <button onClick={() => window.auroraAPI?.browser?.back()}
                    style={btnStyle} title="Back">◀</button>
                <button onClick={() => window.auroraAPI?.browser?.forward()}
                    style={btnStyle} title="Forward">▶</button>
                <button onClick={() => window.auroraAPI?.browser?.reload()}
                    style={btnStyle} title="Reload">↻</button>
                <input
                    type="text"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Enter URL..."
                    style={{
                        flex: 1, padding: "6px 10px", borderRadius: 6,
                        border: "1px solid var(--border, #252636)",
                        background: "var(--bg-input, #0d1117)",
                        color: "var(--text, #c0caf5)", fontSize: 13,
                        outline: "none",
                    }}
                />
                <button onClick={navigate}
                    style={{ ...btnStyle, background: "var(--accent, #7c3aed)", color: "#fff" }}>
                    Go
                </button>
            </div>

            {/* AI Control Banner */}
            {aiControlling && (
                <div style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 12px",
                    background: "linear-gradient(90deg, rgba(124,58,237,0.2), rgba(124,58,237,0.05))",
                    borderBottom: "1px solid rgba(124,58,237,0.3)",
                    fontSize: 11, color: "#a78bfa",
                }}>
                    <span style={{
                        display: "inline-block", width: 8, height: 8, borderRadius: "50%",
                        background: "#a78bfa", animation: "pulse 1.5s infinite",
                    }} />
                    🤖 AI is controlling this browser
                </div>
            )}

            {/* Bookmarks */}
            <div style={{
                display: "flex", gap: 4, padding: "6px 10px",
                borderBottom: "1px solid var(--border, #252636)",
                flexWrap: "wrap",
            }}>
                {BOOKMARKS.map((bm) => (
                    <button key={bm.name}
                        onClick={() => { setUrl(bm.url); openBrowser(bm.url); }}
                        title={bm.label}
                        style={{
                            ...btnStyle, fontSize: 11, padding: "4px 10px",
                            background: currentUrl.includes(bm.name)
                                ? "var(--accent, #7c3aed)" : "var(--bg-button, #252636)",
                            color: currentUrl.includes(bm.name) ? "#fff" : "var(--text-dim, #565f89)",
                        }}
                    >
                        {bm.icon} {bm.label}
                    </button>
                ))}
            </div>

            {/* Status / Error */}
            {error && (
                <div style={{ padding: "8px 12px", color: "#f7768e", fontSize: 12, background: "rgba(247,118,142,0.1)" }}>
                    {error}
                </div>
            )}
            {currentUrl && !error && (
                <div style={{ padding: "4px 12px", color: "var(--text-dim, #565f89)", fontSize: 11 }}>
                    {currentUrl}
                </div>
            )}

            {/* Info when no browser */}
            {!currentUrl && !error && (
                <div style={{
                    flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
                    color: "var(--text-dim, #565f89)", fontSize: 13,
                    flexDirection: "column", gap: 12, padding: 20,
                }}>
                    <div style={{ fontSize: 48 }}>🌐</div>
                    <div>Embedded Browser</div>
                    <div style={{ fontSize: 11 }}>
                        Enter a URL above or click a bookmark
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-faint, #3b4261)", marginTop: 8 }}>
                        Supports: Bilibili · Douyin · GitHub · Xiaohongshu · Google · StackOverflow · npm · PyPI
                    </div>
                </div>
            )}
        </div>
    );
}

const btnStyle: React.CSSProperties = {
    padding: "4px 8px", borderRadius: 4,
    border: "none", cursor: "pointer", fontSize: 14,
    background: "var(--bg-button, #252636)",
    color: "var(--text-dim, #565f89)",
    transition: "all 0.15s",
};
