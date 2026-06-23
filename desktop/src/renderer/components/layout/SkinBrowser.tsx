import React, { useState, useEffect, useCallback } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";
import { applyTheme, getStoredThemeName } from "../../theme";

interface SkinInfo {
    name: string;
    label: string;
    description: string;
    author: string;
    accent: string;
    accent_color_name: string;
    is_builtin: boolean;
    bg_preview: string;
    surface_preview: string;
    text_preview: string;
}

function csVarName(key: string): string {
    return `--aurora-${key.replace(/_/g, "-")}`;
}

export function SkinBrowser({ onClose }: { onClose: () => void }) {
    const colors = useStore((s) => s.themeColors);
    const [skins, setSkins] = useState<SkinInfo[]>([]);
    const [active, setActive] = useState<string>(() => getStoredThemeName());
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const fetchSkins = useCallback(async () => {
        try {
            setLoading(true);
            const res = await fetch("http://127.0.0.1:9876/skins");
            if (!res.ok) throw new Error("Failed to fetch");
            const data = await res.json();
            setSkins(data);
        } catch (e: any) {
            setError(e.message || "Cannot connect to backend");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchSkins();
    }, [fetchSkins]);

    const handleApply = async (name: string) => {
        try {
            const res = await fetch(`http://127.0.0.1:9876/skins/${name}/apply`, { method: "POST" });
            if (!res.ok) throw new Error("Apply failed");
            // Fetch full CSS vars and apply
            const activeRes = await fetch("http://127.0.0.1:9876/skins/active");
            const activeData = await activeRes.json();
            const root = document.documentElement;

            // Apply colors
            if (activeData.colors) {
                for (const [key, val] of Object.entries(activeData.colors)) {
                    root.style.setProperty(csVarName(key), val as string);
                }
            }
            // Apply typography
            if (activeData.typography) {
                root.style.setProperty("--aurora-font-family", activeData.typography.font_family);
                root.style.setProperty("--aurora-font-mono", activeData.typography.font_mono);
                root.style.setProperty("--aurora-font-size-sm", activeData.typography.font_size_sm);
                root.style.setProperty("--aurora-font-size-base", activeData.typography.font_size_base);
                root.style.setProperty("--aurora-font-size-lg", activeData.typography.font_size_lg);
            }
            // Apply shape
            if (activeData.shape) {
                for (const [key, val] of Object.entries(activeData.shape)) {
                    root.style.setProperty(csVarName(key), val as string);
                }
            }

            root.setAttribute("data-theme", name);
            localStorage.setItem("aurora_active_theme", name);
            setActive(name);

            // Update store with core colors
            const c = activeData.colors || {};
            useStore.setState({
                themeColors: {
                    bg: c.bg || "#0d1117",
                    surface: c.surface || "#161b22",
                    border: c.border || "#30363d",
                    text: c.text || "#e6edf3",
                    textSecondary: c.text_secondary || "#8b949e",
                    accent: c.accent || "#8b5cf6",
                    error: c.error || "#f85149",
                    success: c.success || "#3fb950",
                    warning: c.warning || "#d29922",
                    code: c.code_bg || "#0d1117",
                    bgSecondary: c.surface || "#161b22",
                    accentHover: c.accent_hover || c.accent || "#a78bfa",
                } as any,
            });
        } catch (e: any) {
            console.error("Skin apply error:", e);
        }
    };

    if (loading) {
        return (
            <div className="aurora-overlay">
                <div className="aurora-panel skin-browser" style={{ backgroundColor: colors.surface, borderColor: colors.border, color: colors.text }}>
                    <div className="aurora-panel-header" style={{ borderColor: colors.border }}>
                        <span>{t("skins") || "Skins"}</span>
                        <button onClick={onClose} style={{ color: colors.textSecondary }}>X</button>
                    </div>
                    <div className="skin-loading" style={{ color: colors.textSecondary, padding: "2rem", textAlign: "center" }}>
                        Loading skins...
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="aurora-overlay">
            <div className="aurora-panel skin-browser" style={{ backgroundColor: colors.surface, borderColor: colors.border, color: colors.text }}>
                <div className="aurora-panel-header" style={{ borderColor: colors.border }}>
                    <span>{t("skins") || "Skins"}</span>
                    <button onClick={onClose} style={{ color: colors.textSecondary }}>X</button>
                </div>
                {error ? (
                    <div style={{ color: colors.error, padding: "1rem" }}>{error}</div>
                ) : (
                    <div className="skin-grid">
                        {skins.map((skin) => {
                            const isActive = skin.name === active;
                            return (
                                <div
                                    key={skin.name}
                                    className={`skin-card ${isActive ? "active" : ""}`}
                                    style={{
                                        border: isActive ? `2px solid ${skin.accent}` : `1px solid ${colors.border}`,
                                        backgroundColor: skin.bg_preview,
                                        color: skin.text_preview,
                                        borderRadius: "12px",
                                        padding: "14px",
                                        cursor: "pointer",
                                        transition: "all 0.15s ease",
                                    }}
                                    onClick={() => handleApply(skin.name)}
                                >
                                    <div className="skin-card-header">
                                        <div className="skin-accent-dot" style={{ backgroundColor: skin.accent, width: 12, height: 12, borderRadius: "50%" }} />
                                        <span style={{ fontWeight: 600, fontSize: "13px" }}>{skin.label}</span>
                                        {isActive && <span style={{ color: skin.accent, fontSize: "11px", marginLeft: "auto" }}>Active</span>}
                                    </div>
                                    <div className="skin-card-swatches" style={{ display: "flex", gap: "4px", marginTop: "10px" }}>
                                        <span className="swatch" style={{ backgroundColor: skin.bg_preview, width: 20, height: 20, borderRadius: "4px", border: `1px solid ${colors.border}` }} title="bg" />
                                        <span className="swatch" style={{ backgroundColor: skin.surface_preview, width: 20, height: 20, borderRadius: "4px", border: `1px solid ${colors.border}` }} title="surface" />
                                        <span className="swatch" style={{ backgroundColor: skin.accent, width: 20, height: 20, borderRadius: "4px" }} title="accent" />
                                        <span className="swatch" style={{ backgroundColor: skin.text_preview, width: 20, height: 20, borderRadius: "4px", border: `1px solid ${colors.border}` }} title="text" />
                                    </div>
                                    <div style={{ fontSize: "11px", color: colors.textSecondary, marginTop: "6px" }}>
                                        {skin.accent_color_name}{skin.is_builtin ? " . built-in" : ""}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}
