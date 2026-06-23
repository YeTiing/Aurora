import React, { useState, useEffect, useCallback } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";
import { themes, useTheme } from "../../theme";
import { getShortcutManager, defaultShortcuts } from "../../shortcuts";
import type { Theme } from "../../theme";

export function SettingsPanel({ onClose }: { onClose: () => void }) {
    const colors = useStore((s) => s.themeColors);
    const [activeTheme, setActiveTheme] = useTheme();
    const fontSize = useStore((s) => s.editorFontSize);
    const setEditorFontSize = useStore((s) => s.setEditorFontSize);
    const tabSize = useStore((s) => s.editorTabSize);
    const setEditorTabSize = useStore((s) => s.setEditorTabSize);
    const wordWrap = useStore((s) => s.editorWordWrap);
    const setEditorWordWrap = useStore((s) => s.setEditorWordWrap);
    const minimap = useStore((s) => s.editorMinimap);
    const setEditorMinimap = useStore((s) => s.setEditorMinimap);
    const terminalFontSize = useStore((s) => s.terminalFontSize);
    const setTerminalFontSize = useStore((s) => s.setTerminalFontSize);
    const backendConnected = useStore((s) => s.backendConnected);

    // LLM settings state
    // LLM settings from store — stays sync'd across components
    const provider = useStore((s) => s.llmProvider);
    const model = useStore((s) => s.llmModel);
    const baseUrl = useStore((s) => s.llmBaseUrl);
    const maxContext = useStore((s) => s.llmMaxContext);
    const temperature = useStore((s) => s.llmTemperature);
    const setProvider = useStore((s) => s.setLLMProvider);
    const setModel = useStore((s) => s.setLLMModel);
    const setBaseUrl = useStore((s) => s.setLLMBaseUrl);
    const setMaxContext = useStore((s) => s.setLLMMaxContext);
    const setTemperature = useStore((s) => s.setLLMTemperature);
    const reloadSettings = useStore((s) => s.reloadLLMSettings);
    const [apiKey, setApiKey] = useState("");
    const [modelsList, setModelsList] = useState<string[]>([]);
    const [testResult, setTestResult] = useState<{ok?:boolean;response?:string;error?:string} | null>(null);
    const [fetchingModels, setFetchingModels] = useState(false);
    const [customModels, setCustomModels] = useState<string[]>([]);
    const [saving, setSaving] = useState(false);

    // Vision fallback state
    const [visionEnabled, setVisionEnabled] = useState(true);
    const [visionModel, setVisionModel] = useState("gpt-4o-mini");
    const [visionApiKey, setVisionApiKey] = useState("");
    const [visionBaseUrl, setVisionBaseUrl] = useState("");
    const [visionLoaded, setVisionLoaded] = useState(false);

    // Load vision settings
    useEffect(() => {
        if (visionLoaded) return;
        fetch("http://127.0.0.1:9876/settings")
            .then(r => r.json())
            .then(data => {
                const vf = data.vision_fallback || {};
                if (vf.model) setVisionModel(vf.model);
                if (vf.api_key) setVisionApiKey(vf.api_key);
                if (vf.base_url) setVisionBaseUrl(vf.base_url);
                if (vf.enabled !== undefined) setVisionEnabled(vf.enabled);
                setVisionLoaded(true);
            })
            .catch(() => setVisionLoaded(true));
    }, [visionLoaded]);

    const [activeTab, setActiveTab] = useState<"general" | "llm" | "vision" | "editor" | "terminal" | "shortcuts">("llm");
    const [shortcutBindings, setShortcutBindings] = useState<
        Array<{ id: string; label: string; display: string; defaultDisplay: string; isCustom: boolean }>
    >([]);

    const tabs: Array<{ id: typeof activeTab; label: string }> = [
        { id: "general", label: "设置" },
        { id: "llm", label: "LLM API" },
        { id: "vision", label: "Vision" },
        { id: "editor", label: "编辑器" },
        { id: "terminal", label: "终端" },
        { id: "shortcuts", label: "快捷键" },
    ];

    // Load settings
    useEffect(() => {
        reloadSettings();
    }, [reloadSettings]);

    // Load shortcut bindings
    useEffect(() => {
        const sm = getShortcutManager();
        setShortcutBindings(sm.getAllBindings());
    }, [activeTab]);

    // Fetch models
    const fetchModels = useCallback(async () => {
        try {
            const r = await fetch("http://127.0.0.1:9876/models");
            const data = await r.json();
            setModelsList(data.models?.map((m: any) => m.id) || []);
        } catch { setModelsList([]); }
    }, []);

    useEffect(() => { fetchModels(); }, [fetchModels]);

    // Save
    
    const fetchRemoteModels = async () => {
        if (!baseUrl) return;
        setFetchingModels(true);
        try {
            let url = baseUrl.endsWith("/") ? baseUrl + "models" : baseUrl + "/models";
            if (!url.includes("/v1/")) {
                url = baseUrl.endsWith("/") ? baseUrl + "v1/models" : baseUrl + "/v1/models";
            }
            const actualUrl = baseUrl.endsWith("/v1") ? baseUrl + "/models" : url;
            
            const res = await fetch(actualUrl, {
                headers: { "Authorization": `Bearer ${apiKey}` }
            });
            if (res.ok) {
                const data = await res.json();
                if (data && data.data && Array.isArray(data.data)) {
                    const ids = data.data.map((m: any) => m.id);
                    setCustomModels(ids);
                    if (ids.length > 0 && !ids.includes(model)) {
                        setModel(ids[0]);
                    }
                }
            }
        } catch (e) {
            console.error("Failed to fetch models", e);
        }
        setFetchingModels(false);
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const body: any = {};
            if (provider) body.provider = provider;
            if (model) body.model = model;
            if (apiKey && apiKey.trim()) body.api_key = apiKey.trim();
            if (baseUrl) body.base_url = baseUrl;
            if (maxContext) body.max_context_tokens = maxContext;
            if (temperature !== undefined) body.temperature = temperature;
            if (visionModel || visionApiKey || visionBaseUrl || !visionEnabled) {
                body.vision_fallback = {
                    enabled: visionEnabled,
                    model: visionModel || "gpt-4o-mini",
                    provider: "openai",
                };
                if (visionApiKey.trim()) body.vision_fallback.api_key = visionApiKey.trim();
                if (visionBaseUrl.trim()) body.vision_fallback.base_url = visionBaseUrl.trim();
            }

            const r = await fetch("http://127.0.0.1:9876/settings", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            });
            const data = await r.json();
            if (data.ok) { 
                setApiKey(""); 
                setTestResult({ok: true, response: t("settingsSaved")});
                // Sync store
                if (provider) setProvider(provider);
                if (model) setModel(model);
                if (baseUrl) setBaseUrl(baseUrl);
                if (maxContext) setMaxContext(maxContext);
                if (temperature !== undefined) setTemperature(temperature);
            } else {
                setTestResult({ok: false, error: data.detail || t("saveFailed")});
            }
        } catch(e) { }
        setSaving(false);
    };

    // Test connection
    const handleTest = async () => {
        setTestResult(null);
        try {
            const body: any = { provider, model };
            if (apiKey && apiKey.trim()) body.api_key = apiKey.trim();
            if (baseUrl) body.base_url = baseUrl;

            await fetch("http://127.0.0.1:9876/settings", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            });

            const r = await fetch("http://127.0.0.1:9876/llm/test", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({message: "Say hello in 3 words"}),
            });
            const data = await r.json();
            setTestResult(data);
            if (data.ok) fetchModels();
        } catch(e: any) { setTestResult({ok: false, error: e.message}); }
    };

    const handleResetShortcut = (id: string) => {
        const sm = getShortcutManager();
        sm.resetOverride(id);
        setShortcutBindings(sm.getAllBindings());
    };

    const handleStartEditShortcut = (id: string) => {
        const sm = getShortcutManager();
        const listener = (e: KeyboardEvent) => {
            if ([t("ctrl"),t("alt"),t("shift"),t("meta")].includes(e.key)) return;
            const mods = { ctrl: e.ctrlKey, alt: e.altKey, shift: e.shiftKey, meta: e.metaKey };
            sm.setOverride(id, mods, e.key);
            setShortcutBindings(sm.getAllBindings());
            window.removeEventListener("keydown", listener);
        };
        window.addEventListener("keydown", listener);
        setTimeout(() => window.removeEventListener("keydown", listener), 5000);
    };

    const inputStyle: React.CSSProperties = {
        width: "100%", padding: "6px 8px", fontSize: 12,
        backgroundColor: colors.bg, color: colors.text,
        border: `1px solid ${colors.border}`, borderRadius: 4,
        outline: "none",
    };

    return (
        <div style={{
            position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
            zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center",
        }}>
            <div style={{
                position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
                backgroundColor: "rgba(0,0,0,0.5)",
            }} onClick={onClose} />

            <div style={{
                position: "relative", width: 520, maxHeight: "80vh",
                backgroundColor: colors.surface, borderRadius: 12,
                border: `1px solid ${colors.border}`,
                display: "flex", flexDirection: "column",
                boxShadow: "0 16px 48px rgba(0,0,0,0.4)",
                overflow: "hidden",
            }}>
                {/* Header */}
                <div style={{
                    padding: "12px 16px", borderBottom: `1px solid ${colors.border}`,
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    flexShrink: 0,
                }}>
                    <span style={{ fontSize: 14, fontWeight: 600, color: colors.text }}>设置</span>
                    <button onClick={onClose}
                        style={{ background: "none", border: "none", color: colors.textSecondary, cursor: "pointer", fontSize: 16 }}>
                        ×
                    </button>
                </div>

                {/* Tabs */}
                <div style={{
                    display: "flex", borderBottom: `1px solid ${colors.border}`,
                    padding: "0 12px", gap: 4, flexShrink: 0,
                }}>
                    {tabs.map(tab => (
                        <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                            style={{
                                padding: "6px 12px", fontSize: 11, fontWeight: 500,
                                cursor: "pointer", border: "none", background: "none",
                                color: activeTab === tab.id ? colors.accent : colors.textSecondary,
                                borderBottom: activeTab === tab.id ? `2px solid ${colors.accent}` : "2px solid transparent",
                            }}>
                            {tab.label}
                        </button>
                    ))}
                </div>

                {/* Content */}
                <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
                    {activeTab === "llm" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                            {/* Saved API configs */}
                            <div>
                                <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>API 配置</label>
                                <div style={{ display: "flex", gap: 6 }}>
                                    <select value={provider + "|" + baseUrl} onChange={e => {
                                        const [p, u] = e.target.value.split("|");
                                        setProvider(p); setBaseUrl(u);
                                    }} style={{ ...inputStyle, flex: 1 }}>
                                        <option value="openai|https://api.openai.com/v1">OpenAI</option>
                                        <option value="anthropic|https://api.anthropic.com">Anthropic</option>
                                        <option value="deepseek|https://api.deepseek.com">DeepSeek</option>
                                        <option value="groq|https://api.groq.com/openai/v1">Groq</option>
                                        <option value="moonshot|https://api.moonshot.cn/v1">Moonshot</option>
                                        <option value="openrouter|https://openrouter.ai/api/v1">OpenRouter</option>
                                        <option value="custom">Custom</option>
                                    </select>
                                    <button onClick={() => { setProvider("custom"); setBaseUrl(""); }}
                                        style={{ padding: "6px 10px", background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 4, color: colors.text, cursor: "pointer", fontSize: 11 }}>自定义</button>
                                </div>
                            </div>
                            <div>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                                    <label style={{ fontSize: 11, color: colors.textSecondary }}>Model</label>
                                    <button 
                                        onClick={fetchRemoteModels}
                                        disabled={fetchingModels || !baseUrl}
                                        style={{ background: "transparent", border: "none", color: colors.accent, fontSize: 11, cursor: baseUrl ? "pointer" : "not-allowed", opacity: baseUrl ? 1 : 0.5 }}
                                    >
                                        {fetchingModels ? "获取中..." : "🔄 获取远端模型"}
                                    </button>
                                </div>
                                <select value={model} onChange={e => setModel(e.target.value)} style={inputStyle}>
                                    {customModels.length > 0 ? (
                                        customModels.map(m => <option key={m} value={m}>{m}</option>)
                                    ) : (
                                        <>
                                            {modelsList.map(m => <option key={m} value={m}>{m}</option>)}
                                            {modelsList.length === 0 && (
                                                <>
                                                    <option value="gpt-4o">gpt-4o</option>
                                                    <option value="gpt-4o-mini">gpt-4o-mini</option>
                                                    <option value="deepseek-chat">deepseek-chat</option>
                                                    <option value={model}>{model}</option>
                                                </>
                                            )}
                                        </>
                                    )}
                                </select>
                            </div>
                            <div>
                                <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>API Key</label>
                                <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
                                    placeholder="sk-..." style={inputStyle} />
                            </div>
                            <div>
                                <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>Base URL</label>
                                <input type="text" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} style={inputStyle} />
                            </div>

                            {/* Manual number inputs */}
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                                <div>
                                    <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>上下文窗口 (tokens)</label>
                                    <input type="number" value={maxContext} min={1000} max={500000} step={1000}
                                        onChange={e => setMaxContext(parseInt(e.target.value) || 24000)}
                                        style={inputStyle} />
                                </div>
                                <div>
                                    <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>Temperature</label>
                                    <input type="number" value={temperature} min={0} max={2} step={0.05}
                                        onChange={e => setTemperature(parseFloat(e.target.value) || 0.3)}
                                        style={inputStyle} />
                                </div>
                            </div>

                            <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                                <button onClick={handleSave} disabled={saving}
                                    style={{ flex: 1, padding: "8px", background: colors.accent, color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 12, opacity: saving ? 0.7 : 1 }}>
                                    {saving ? t("saving") : t("saveSettings")}
                                </button>
                                <button onClick={handleTest}
                                    style={{ flex: 1, padding: "8px", background: colors.success, color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 12 }}>
                                    测试连接
                                </button>
                            </div>

                            {testResult && (
                                <div style={{
                                    padding: 8, borderRadius: 4, fontSize: 12,
                                    backgroundColor: testResult.ok ? "rgba(63,185,80,0.1)" : "rgba(248,81,73,0.1)",
                                    color: testResult.ok ? colors.success : colors.error,
                                    border: `1px solid ${testResult.ok ? colors.success : colors.error}`,
                                }}>
                                    {testResult.ok
                                        ? `OK: ${testResult.response}`
                                        : `ERROR: ${testResult.error || t("unknownError")}`
                                    }
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === "general" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                            <div>
                                <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 6 }}>
                                    Theme
                                </label>
                                <select
                                    value={activeTheme.name}
                                    onChange={(e) => setActiveTheme(e.target.value)}
                                    style={inputStyle}
                                >
                                    {themes.map((t) => (
                                        <option key={t.name} value={t.name}>{t.label}</option>
                                    ))}
                                </select>
                            </div>
                            <div style={{
                                display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6,
                            }}>
                                {Object.entries(activeTheme.colors).map(([key, val]) => (
                                    <div key={key} style={{
                                        display: "flex", alignItems: "center", gap: 6,
                                        padding: "4px 8px", borderRadius: 4,
                                        backgroundColor: colors.bgSecondary,
                                        border: `1px solid ${colors.border}`,
                                    }}>
                                        <div style={{
                                            width: 14, height: 14, borderRadius: 3,
                                            backgroundColor: val,
                                            border: `1px solid ${colors.border}`,
                                            flexShrink: 0,
                                        }} />
                                        <span style={{ fontSize: 10, color: colors.textSecondary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                            {key}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {activeTab === "vision" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                            <div style={{ fontSize: 12, color: colors.textSecondary, lineHeight: 1.5 }}>
                                When main model can&apos;t see images, auto-fallback to a vision-capable model.
                                Zero-config: reuses your LLM API key by default.
                            </div>

                            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: colors.text }}>
                                <input type="checkbox" checked={visionEnabled} onChange={e => setVisionEnabled(e.target.checked)} />
                                Enable Vision Fallback
                            </label>

                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                <label style={{ fontSize: 11, color: colors.textSecondary }}>Vision Model</label>
                                <select
                                    value={visionModel}
                                    onChange={e => setVisionModel(e.target.value)}
                                    style={{
                                        background: colors.bg, color: colors.text, border: `1px solid ${colors.border}`,
                                        borderRadius: 6, padding: "6px 10px", fontSize: 12, outline: "none",
                                    }}
                                >
                                    <option value="gpt-4o-mini">gpt-4o-mini (cheapest)</option>
                                    <option value="gpt-4o">gpt-4o (best multimodal)</option>
                                    <option value="gpt-4-turbo">gpt-4-turbo</option>
                                    <option value="claude-3-haiku">claude-3-haiku</option>
                                    <option value="claude-3-5-sonnet">claude-3-5-sonnet</option>
                                </select>
                            </div>

                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                <label style={{ fontSize: 11, color: colors.textSecondary }}>
                                    Vision API Key <span style={{ color: colors.textMuted || colors.textSecondary }}>(optional — reuses main LLM key)</span>
                                </label>
                                <input
                                    type="password"
                                    value={visionApiKey}
                                    onChange={e => setVisionApiKey(e.target.value)}
                                    placeholder="Leave empty to use main API key"
                                    style={{
                                        background: colors.bg, color: colors.text, border: `1px solid ${colors.border}`,
                                        borderRadius: 6, padding: "6px 10px", fontSize: 12, outline: "none",
                                    }}
                                />
                            </div>

                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                <label style={{ fontSize: 11, color: colors.textSecondary }}>
                                    Vision Base URL <span style={{ color: colors.textMuted || colors.textSecondary }}>(optional)</span>
                                </label>
                                <input
                                    type="text"
                                    value={visionBaseUrl}
                                    onChange={e => setVisionBaseUrl(e.target.value)}
                                    placeholder="Leave empty to use main base URL"
                                    style={{
                                        background: colors.bg, color: colors.text, border: `1px solid ${colors.border}`,
                                        borderRadius: 6, padding: "6px 10px", fontSize: 12, outline: "none",
                                    }}
                                />
                            </div>
                        </div>
                    )}

                    {activeTab === "editor" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                            <label style={{ fontSize: 11, color: colors.textSecondary }}>Font Size: {fontSize}px</label>
                            <input type="range" min={10} max={30} value={fontSize} onChange={e => setEditorFontSize(parseInt(e.target.value))} />
                            <label style={{ fontSize: 11, color: colors.textSecondary }}>Tab: {tabSize}</label>
                            <input type="range" min={2} max={8} value={tabSize} onChange={e => setEditorTabSize(parseInt(e.target.value))} />
                            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: colors.text }}>
                                <input type="checkbox" checked={wordWrap} onChange={e => setEditorWordWrap(e.target.checked)} /> Word Wrap
                            </label>
                            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: colors.text }}>
                                <input type="checkbox" checked={minimap} onChange={e => setEditorMinimap(e.target.checked)} /> Minimap
                            </label>
                        </div>
                    )}

                    {activeTab === "terminal" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                            <label style={{ fontSize: 11, color: colors.textSecondary }}>Terminal Font: {terminalFontSize}px</label>
                            <input type="range" min={10} max={24} value={terminalFontSize} onChange={e => setTerminalFontSize(parseInt(e.target.value))} />
                        </div>
                    )}

                    {activeTab === "shortcuts" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                            <div style={{
                                fontSize: 10, color: colors.textSecondary,
                                marginBottom: 4, fontStyle: "italic",
                            }}>
                                Click a shortcut to rebind; press desired key combination.
                            </div>
                            {shortcutBindings.map(s => (
                                <div key={s.id} style={{
                                    display: "flex", justifyContent: "space-between", alignItems: "center",
                                    padding: "6px 8px", fontSize: 12,
                                    borderRadius: 4,
                                    backgroundColor: s.isCustom ? "rgba(88,166,255,0.08)" : "transparent",
                                    border: `1px solid ${colors.border}`,
                                    cursor: "pointer",
                                }}
                                onClick={() => handleStartEditShortcut(s.id)}
                                >
                                    <span style={{ color: colors.text }}>{s.label}</span>
                                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                        <code style={{
                                            background: colors.bg,
                                            padding: "2px 8px", borderRadius: 3,
                                            color: s.isCustom ? colors.accent : colors.textSecondary,
                                            fontSize: 11,
                                        }}>
                                            {s.display}
                                        </code>
                                        {s.isCustom && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); handleResetShortcut(s.id); }}
                                                title={t("resetToDefault")}
                                                style={{
                                                    background: "none", border: "none",
                                                    color: colors.textSecondary,
                                                    cursor: "pointer", fontSize: 13,
                                                    lineHeight: 1, padding: "0 2px",
                                                }}
                                            >
                                                Reset
                                            </button>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
