import React, { useState, useEffect, useCallback } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";

interface RESession {
    id: string;
    url: string;
    scene: string;
    total: number;
    apis: number;
    js_files: number;
    hooks: number;
    db_size: number;
}

interface RERequest {
    id: string; seq: number; method: string;
    path: string; url: string;
    response_status: number; content_type: string;
    is_js: boolean; is_streaming: boolean;
}

interface REAnalysis {
    session_id?: string;
    scenes?: Array<{ scene: string; score: number }>;
    auth_tokens?: Array<{ type: string; value: string }>;
    crypto?: Array<{ algorithm: string; count: number }>;
    api_endpoints?: string[];
    stats?: { total: number; apis: number; js_files: number; hooks: number };
}

export function RePanel({ onClose }: { onClose: () => void }) {
    const colors = useStore((s) => s.themeColors);
    const [sessions, setSessions] = useState<RESession[]>([]);
    const [activeSession, setActiveSession] = useState<string>("");
    const [requests, setRequests] = useState<RERequest[]>([]);
    const [analysis, setAnalysis] = useState<REAnalysis | null>(null);
    const [captureUrl, setCaptureUrl] = useState("");
    const [capturing, setCapturing] = useState(false);
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState<"sessions" | "requests" | "analysis">("sessions");
    const [error, setError] = useState("");

    const fetchSessions = useCallback(async () => {
        try {
            const r = await fetch("http://127.0.0.1:9876/re/sessions");
            const data = await r.json();
            setSessions(data);
        } catch (e: any) {
            setError(e.message);
        }
    }, []);

    useEffect(() => { fetchSessions(); }, [fetchSessions]);

    useEffect(() => {
        if (capturing) {
            const interval = setInterval(fetchSessions, 2000);
            return () => clearInterval(interval);
        }
    }, [capturing, fetchSessions]);

    const startCapture = async () => {
        setLoading(true);
        try {
            const r = await fetch("http://127.0.0.1:9876/re/capture/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: captureUrl }),
            });
            const data = await r.json();
            setCapturing(true);
            setActiveSession(data.session_id);
            fetchSessions();
        } catch (e: any) { setError(e.message); }
        setLoading(false);
    };

    const stopCapture = async () => {
        await fetch("http://127.0.0.1:9876/re/capture/stop", { method: "POST" });
        setCapturing(false);
        fetchSessions();
    };

    const loadRequests = async (sid: string) => {
        setActiveSession(sid);
        setLoading(true);
        try {
            const r = await fetch(`http://127.0.0.1:9876/re/sessions/${sid}/requests?api_only=true`);
            const data = await r.json();
            setRequests(data.requests || []);
            setActiveTab("requests");
        } catch (e: any) { setError(e.message); }
        setLoading(false);
    };

    const runAnalysis = async (sid: string) => {
        setLoading(true);
        try {
            const r = await fetch("http://127.0.0.1:9876/re/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: sid }),
            });
            const data = await r.json();
            setAnalysis(data);
            setActiveTab("analysis");
        } catch (e: any) { setError(e.message); }
        setLoading(false);
    };

    const deleteSession = async (sid: string) => {
        await fetch(`http://127.0.0.1:9876/re/sessions/${sid}`, { method: "DELETE" });
        fetchSessions();
    };

    const statusColors: Record<number, string> = { 2: "#3fb950", 3: "#d29922", 4: "#f85149", 5: "#f85149" };
    const getStatusColor = (s: number) => statusColors[Math.floor(s / 100)] || colors.textSecondary;

    return (
        <div className="aurora-overlay">
            <div className="aurora-panel re-panel" style={{ backgroundColor: colors.surface, borderColor: colors.border, color: colors.text, width: 780, maxWidth: "95vw", maxHeight: "85vh" }}>
                <div className="aurora-panel-header" style={{ borderColor: colors.border }}>
                    <span>RE Workspace</span>
                    <div style={{ display: "flex", gap: 8 }}>
                        {!capturing ? (
                            <button className="toolbar-btn" onClick={startCapture} style={{ fontSize: 11, padding: "4px 10px" }}>
                                + Capture
                            </button>
                        ) : (
                            <button className="toolbar-btn" onClick={stopCapture} style={{ fontSize: 11, padding: "4px 10px", background: colors.error, color: "#fff" }}>
                                Stop
                            </button>
                        )}
                        <button onClick={onClose} style={{ color: colors.textSecondary, background: "none", border: "none", cursor: "pointer", fontSize: 16 }}>X</button>
                    </div>
                </div>

                {/* Capture bar */}
                <div style={{ padding: "8px 16px", display: "flex", gap: 8, borderBottom: `1px solid ${colors.border}` }}>
                    <input
                        value={captureUrl}
                        onChange={e => setCaptureUrl(e.target.value)}
                        placeholder="https://target.com"
                        style={{ flex: 1, background: colors.bg, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: 6, padding: "5px 10px", fontSize: 12 }}
                    />
                    {capturing && <span style={{ color: colors.success, fontSize: 12, whiteSpace: "nowrap" }}>Capturing...</span>}
                </div>

                {/* Tabs */}
                <div style={{ display: "flex", borderBottom: `1px solid ${colors.border}` }}>
                    {["sessions", "requests", "analysis"].map(tab => (
                        <button key={tab} onClick={() => { setActiveTab(tab as any); if (tab === "requests" && activeSession) loadRequests(activeSession); }}
                            style={{
                                padding: "8px 16px", fontSize: 12, border: "none", background: activeTab === tab ? colors.accent : "transparent",
                                color: activeTab === tab ? "#fff" : colors.textSecondary, cursor: "pointer",
                                borderBottom: activeTab === tab ? `2px solid ${colors.accent}` : "2px solid transparent",
                            }}>
                            {tab === "sessions" ? "Sessions" : tab === "requests" ? "APIs" : "Analysis"}
                        </button>
                    ))}
                </div>

                <div style={{ overflow: "auto", maxHeight: "60vh", padding: 12 }}>
                    {error && <div style={{ color: colors.error, marginBottom: 8, fontSize: 12 }}>{error}</div>}

                    {/* Sessions */}
                    {activeTab === "sessions" && (
                        <div>
                            {sessions.length === 0 ? (
                                <div style={{ color: colors.textSecondary, fontSize: 12, textAlign: "center", padding: 20 }}>
                                    No RE sessions. Start a capture above.
                                </div>
                            ) : sessions.map(s => (
                                <div key={s.id} style={{
                                    padding: "10px 12px", margin: "4px 0", borderRadius: 8,
                                    background: s.id === activeSession ? colors.accent + "18" : colors.bgSecondary,
                                    border: `1px solid ${s.id === activeSession ? colors.accent : colors.border}`,
                                    cursor: "pointer",
                                }}>
                                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                                        <div style={{ fontWeight: 600, fontSize: 12 }}>{s.url || "Manual capture"}</div>
                                        <div style={{ display: "flex", gap: 6 }}>
                                            <button onClick={() => loadRequests(s.id)} style={{ background: colors.accent, color: "#fff", border: "none", borderRadius: 4, padding: "3px 8px", fontSize: 11, cursor: "pointer" }}>APIs</button>
                                            <button onClick={() => runAnalysis(s.id)} style={{ background: "transparent", color: colors.accent, border: `1px solid ${colors.accent}`, borderRadius: 4, padding: "3px 8px", fontSize: 11, cursor: "pointer" }}>Analyze</button>
                                            <button onClick={() => deleteSession(s.id)} style={{ background: "transparent", color: colors.error, border: "none", fontSize: 13, cursor: "pointer" }}>X</button>
                                        </div>
                                    </div>
                                    <div style={{ fontSize: 10, color: colors.textSecondary, marginTop: 4 }}>
                                        {s.apis} APIs | {s.js_files} JS | {s.hooks} hooks | {s.scene} | {(s.db_size / 1024).toFixed(0)} KB
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Requests */}
                    {activeTab === "requests" && (
                        <div>
                            {requests.length === 0 ? (
                                <div style={{ color: colors.textSecondary, fontSize: 12, textAlign: "center", padding: 20 }}>No API requests captured.</div>
                            ) : requests.map(req => (
                                <div key={req.id} style={{
                                    display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", fontSize: 11,
                                    borderBottom: `1px solid ${colors.border}`, cursor: "pointer",
                                }}>
                                    <span style={{ fontWeight: 600, color: getStatusColor(req.response_status), minWidth: 36 }}>{req.method}</span>
                                    <span style={{ color: colors.textSecondary, minWidth: 28, textAlign: "right" }}>{req.response_status}</span>
                                    <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{req.path}</span>
                                    {req.is_js && <span style={{ color: "#F7DF1E", fontSize: 10 }}>JS</span>}
                                    {req.is_streaming && <span style={{ color: colors.accent, fontSize: 10 }}>SSE</span>}
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Analysis */}
                    {activeTab === "analysis" && analysis && (
                        <div style={{ fontSize: 12 }}>
                            {analysis.scenes && analysis.scenes.length > 0 && (
                                <div style={{ marginBottom: 14 }}>
                                    <div style={{ fontWeight: 600, marginBottom: 6, color: colors.accent }}>Scene Detection</div>
                                    {analysis.scenes.map((s: any) => (
                                        <div key={s.scene} style={{ display: "flex", gap: 8, padding: "3px 0" }}>
                                            <span>{s.scene}</span>
                                            <span style={{ color: colors.textSecondary }}>{s.score}%</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                            {analysis.crypto && analysis.crypto.length > 0 && (
                                <div style={{ marginBottom: 14 }}>
                                    <div style={{ fontWeight: 600, marginBottom: 6, color: colors.warning }}>Crypto Fingerprint</div>
                                    {analysis.crypto.map((c: any) => (
                                        <div key={c.algorithm} style={{ display: "flex", gap: 8, padding: "3px 0" }}>
                                            <span>{c.algorithm}</span>
                                            <span style={{ color: colors.textSecondary }}>{c.count} hits</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                            {analysis.auth_tokens && analysis.auth_tokens.length > 0 && (
                                <div style={{ marginBottom: 14 }}>
                                    <div style={{ fontWeight: 600, marginBottom: 6, color: colors.success }}>Auth Tokens</div>
                                    {analysis.auth_tokens.map((t: any, i: number) => (
                                        <div key={i} style={{ padding: "3px 0" }}>
                                            <span style={{ color: colors.textSecondary }}>{t.type}:</span>{" "}
                                            <span style={{ fontFamily: "monospace", wordBreak: "break-all" }}>{t.value}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                            {analysis.api_endpoints && analysis.api_endpoints.length > 0 && (
                                <div>
                                    <div style={{ fontWeight: 600, marginBottom: 6 }}>API Endpoints</div>
                                    {analysis.api_endpoints.map((ep: string, i: number) => (
                                        <div key={i} style={{ fontFamily: "monospace", fontSize: 11, padding: "2px 0", color: colors.textSecondary }}>
                                            {ep}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                    {activeTab === "analysis" && !analysis && (
                        <div style={{ color: colors.textSecondary, fontSize: 12, textAlign: "center", padding: 20 }}>
                            Select a session and click "Analyze".
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
