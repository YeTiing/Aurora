import React, { useEffect, useState, useRef, useCallback } from "react";
import { useStore } from "../../store";

interface StatsData {
    tokens: { used: number; limit: number; pct: number };
    latency: { avg: number; p50: number; p95: number; max: number; recent: number[] };
    errors: { count: number; rate: number };
    tool_calls: Record<string, number>;
    turns: { total: number; avg_duration: number; recent: { ts: number; duration_ms: number; error: boolean }[] };
    rate_limits: { count: number; current_delay: number };
}

const API_BASE = "http://127.0.0.1:2728";

function TokenGauge({ pct, used, limit }: { pct: number; used: number; limit: number }) {
    const angle = Math.min((pct / 100) * 360, 360);
    const strokeColor = pct > 85 ? "#ef4444" : pct > 60 ? "#f59e0b" : "#4ade80";
    const radius = 40;
    const circumference = 2 * Math.PI * radius;
    const dashoffset = circumference - (angle / 360) * circumference;

    return (
        <div style={{ textAlign: "center" }}>
            <div style={{ position: "relative", width: 96, height: 96, margin: "0 auto" }}>
                <svg width={96} height={96} viewBox="0 0 96 96">
                    <circle cx={48} cy={48} r={radius} fill="none" stroke="#333" strokeWidth={6} />
                    <circle
                        cx={48} cy={48} r={radius} fill="none" stroke={strokeColor} strokeWidth={6}
                        strokeDasharray={circumference} strokeDashoffset={dashoffset}
                        strokeLinecap="round" transform="rotate(-90 48 48)"
                        style={{ transition: "stroke-dashoffset 0.6s ease" }}
                    />
                </svg>
                <div style={{
                    position: "absolute", inset: 0, display: "flex",
                    flexDirection: "column", alignItems: "center", justifyContent: "center",
                    fontSize: 12, fontWeight: 700, color: "#e5e7eb",
                }}>
                    <span style={{ fontSize: 16 }}>{pct}%</span>
                    <span style={{ fontSize: 9, color: "#9ca3af" }}>{used.toLocaleString()}/{limit.toLocaleString()}</span>
                </div>
            </div>
            <div style={{ fontSize: 10, color: "#9ca3af", marginTop: 4 }}>Token Usage</div>
        </div>
    );
}

function LatencyChart({ data, avg, p95 }: { data: number[]; avg: number; p95: number }) {
    const maxVal = Math.max(...data, 100);
    return (
        <div>
            <div style={{ fontSize: 10, color: "#9ca3af", marginBottom: 4 }}>
                LLM Latency (last {data.length})  |  avg: {avg.toFixed(0)}ms  |  p95: {p95.toFixed(0)}ms
            </div>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 48, padding: "2px 0" }}>
                {data.map((val, i) => (
                    <div
                        key={i}
                        title={`${val.toFixed(0)}ms`}
                        style={{
                            flex: 1, height: `${Math.max((val / maxVal) * 100, 2)}%`,
                            backgroundColor: val > p95 ? "#ef4444" : val > avg * 2 ? "#f59e0b" : "#4ade80",
                            borderRadius: "1px 1px 0 0",
                            minHeight: 2,
                            transition: "height 0.2s",
                        }}
                    />
                ))}
            </div>
        </div>
    );
}

function ErrorRate({ count, rate }: { count: number; rate: number }) {
    const color = rate < 5 ? "#4ade80" : rate < 15 ? "#f59e0b" : "#ef4444";
    return (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
                width: 12, height: 12, borderRadius: "50%", backgroundColor: color,
                display: "inline-block",
            }} />
            <span style={{ fontSize: 13 }}>
                <span style={{ fontWeight: 700, color }}>{rate.toFixed(1)}%</span>
                <span style={{ color: "#9ca3af", fontSize: 10, marginLeft: 4 }}>({count} errors)</span>
            </span>
        </div>
    );
}

function ToolFrequency({ tools }: { tools: Record<string, number> }) {
    const sorted = Object.entries(tools).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const maxCount = sorted[0]?.[1] ?? 1;
    return (
        <div>
            <div style={{ fontSize: 10, color: "#9ca3af", marginBottom: 4 }}>Tool Call Frequency</div>
            {sorted.map(([name, count]) => (
                <div key={name} style={{ marginBottom: 3 }}>
                    <div style={{
                        display: "flex", justifyContent: "space-between",
                        fontSize: 10, marginBottom: 1,
                    }}>
                        <span style={{ color: "#e5e7eb" }}>{name}</span>
                        <span style={{ color: "#9ca3af" }}>{count}</span>
                    </div>
                    <div style={{
                        height: 4, backgroundColor: "#333", borderRadius: 2,
                        overflow: "hidden",
                    }}>
                        <div style={{
                            height: "100%", width: `${Math.max((count / maxCount) * 100, 2)}%`,
                            backgroundColor: "#6366f1", borderRadius: 2,
                            transition: "width 0.3s",
                        }} />
                    </div>
                </div>
            ))}
            {sorted.length === 0 && (
                <div style={{ fontSize: 10, color: "#6b7280" }}>No tool calls recorded yet</div>
            )}
        </div>
    );
}

function SessionTimeline({ turns, total, avgDuration }: {
    turns: { ts: number; duration_ms: number; error: boolean }[];
    total: number;
    avgDuration: number;
}) {
    const maxDur = Math.max(...turns.map((t) => t.duration_ms), 100);
    return (
        <div>
            <div style={{ fontSize: 10, color: "#9ca3af", marginBottom: 4 }}>
                Session Turns (total: {total}, avg: {avgDuration.toFixed(0)}ms/turn)
            </div>
            <div style={{ maxHeight: 120, overflowY: "auto" }}>
                {[...turns].reverse().map((t, i) => (
                    <div
                        key={i}
                        style={{
                            display: "flex", alignItems: "center", gap: 6,
                            padding: "2px 0", fontSize: 9,
                            borderBottom: "1px solid #1f2937",
                        }}
                    >
                        <span style={{ color: "#6b7280", width: 60, flexShrink: 0 }}>
                            {new Date(t.ts * 1000).toLocaleTimeString()}
                        </span>
                        <div style={{
                            flex: 1, height: 4, backgroundColor: "#333", borderRadius: 2,
                            overflow: "hidden",
                        }}>
                            <div style={{
                                height: "100%",
                                width: `${Math.max((t.duration_ms / maxDur) * 100, 2)}%`,
                                backgroundColor: t.error ? "#ef4444" : "#4ade80",
                                borderRadius: 2,
                            }} />
                        </div>
                        <span style={{ color: t.error ? "#ef4444" : "#9ca3af", width: 50, textAlign: "right" }}>
                            {t.duration_ms.toFixed(0)}ms
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}

export function MonitorPanel({ onClose }: { onClose: () => void }) {
    const colors = useStore((s) => s.themeColors);
    const [stats, setStats] = useState<StatsData | null>(null);
    const [error, setError] = useState<string | null>(null);
    const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const fetchStats = useCallback(async () => {
        try {
            const res = await fetch(`${API_BASE}/observability/stats`, { signal: AbortSignal.timeout(3000) });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            setStats(data);
            setError(null);
        } catch (e: any) {
            if (e.name !== "TimeoutError" && e.name !== "AbortError") {
                setError(e.message || "Failed to fetch");
            }
        }
    }, []);

    useEffect(() => {
        fetchStats();
        intervalRef.current = setInterval(fetchStats, 2000);
        return () => {
            if (intervalRef.current) clearInterval(intervalRef.current);
        };
    }, [fetchStats]);

    return (
        <div style={{
            position: "fixed", top: 0, right: 0, bottom: 0, width: 360,
            backgroundColor: "#111827", borderLeft: "1px solid #374151",
            color: "#e5e7eb", zIndex: 100, overflowY: "auto",
            display: "flex", flexDirection: "column",
            fontFamily: "system-ui, -apple-system, sans-serif",
            boxShadow: "-4px 0 20px rgba(0,0,0,0.5)",
        }}>
            {/* Header */}
            <div style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "10px 14px", borderBottom: "1px solid #374151",
                flexShrink: 0,
            }}>
                <span style={{ fontWeight: 700, fontSize: 14 }}>
                    <span style={{ color: colors.accent }}>📊</span> Agent Monitor
                </span>
                <button
                    onClick={onClose}
                    style={{
                        background: "none", border: "none", color: "#9ca3af",
                        cursor: "pointer", fontSize: 16, padding: "0 4px",
                    }}
                >✕</button>
            </div>

            {/* Content */}
            <div style={{ flex: 1, padding: "12px 14px", overflowY: "auto" }}>
                {error && (
                    <div style={{
                        padding: "8px 12px", backgroundColor: "#7f1d1d33", borderRadius: 6,
                        color: "#fca5a5", fontSize: 11, marginBottom: 12,
                    }}>
                        {error} — waiting for backend...
                    </div>
                )}

                {stats ? (
                    <>
                        {/* Token Gauge */}
                        <div style={{ marginBottom: 16 }}>
                            <TokenGauge pct={stats.tokens.pct} used={stats.tokens.used} limit={stats.tokens.limit} />
                        </div>

                        {/* Error Rate */}
                        <div style={{
                            marginBottom: 14, padding: "8px 10px",
                            backgroundColor: "#1f2937", borderRadius: 6,
                        }}>
                            <ErrorRate count={stats.errors.count} rate={stats.errors.rate} />
                        </div>

                        {/* Latency */}
                        <div style={{
                            marginBottom: 14, padding: "10px",
                            backgroundColor: "#1f2937", borderRadius: 6,
                        }}>
                            <LatencyChart data={stats.latency.recent} avg={stats.latency.avg} p95={stats.latency.p95} />
                        </div>

                        {/* Tool Frequency */}
                        <div style={{
                            marginBottom: 14, padding: "10px",
                            backgroundColor: "#1f2937", borderRadius: 6,
                        }}>
                            <ToolFrequency tools={stats.tool_calls} />
                        </div>

                        {/* Session Timeline */}
                        <div style={{
                            marginBottom: 14, padding: "10px",
                            backgroundColor: "#1f2937", borderRadius: 6,
                        }}>
                            <SessionTimeline
                                turns={stats.turns.recent}
                                total={stats.turns.total}
                                avgDuration={stats.turns.avg_duration}
                            />
                        </div>

                        {/* Rate Limit Status */}
                        <div style={{
                            padding: "10px", backgroundColor: "#1f2937", borderRadius: 6,
                        }}>
                            <div style={{ fontSize: 10, color: "#9ca3af", marginBottom: 4 }}>
                                Rate Limit Status
                            </div>
                            <div style={{
                                display: "flex", justifyContent: "space-between",
                                fontSize: 12,
                            }}>
                                <span style={{ color: "#e5e7eb" }}>
                                    429 count: <span style={{
                                        color: stats.rate_limits.count > 0 ? "#ef4444" : "#4ade80",
                                        fontWeight: 700,
                                    }}>{stats.rate_limits.count}</span>
                                </span>
                                <span style={{ color: "#e5e7eb" }}>
                                    Delay: <span style={{
                                        color: stats.rate_limits.current_delay > 0 ? "#f59e0b" : "#4ade80",
                                        fontWeight: 700,
                                    }}>{stats.rate_limits.current_delay.toFixed(2)}s</span>
                                </span>
                            </div>
                        </div>
                    </>
                ) : (
                    <div style={{
                        display: "flex", alignItems: "center", justifyContent: "center",
                        height: 200, color: "#6b7280", fontSize: 13,
                    }}>
                        {error ? "Backend unavailable" : "Loading metrics..."}
                    </div>
                )}
            </div>
        </div>
    );
}