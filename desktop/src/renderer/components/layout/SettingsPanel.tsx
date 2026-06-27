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
    const [savedProviders, setSavedProviders] = useState<any[]>([]);
    const [newProviderName, setNewProviderName] = useState("");
    const [editingProviderName, setEditingProviderName] = useState<string | null>(null);
    const [showSaveProvider, setShowSaveProvider] = useState(false);
    const loadProviders = async () => { try { setSavedProviders((await (await fetch("http://127.0.0.1:9876/providers")).json()).providers||[]); } catch {} };
    const saveProvider = async () => {
        const name = newProviderName.trim() || provider;
        await fetch("http://127.0.0.1:9876/providers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, provider, api_key: apiKey, base_url: baseUrl, model, max_context_tokens: maxContext }) });
        if (editingProviderName && editingProviderName !== name) {
            await fetch("http://127.0.0.1:9876/providers/" + encodeURIComponent(editingProviderName), { method: "DELETE" });
        }
        setNewProviderName("");
        setEditingProviderName(null);
        setShowSaveProvider(false);
        loadProviders();
    };
    const deleteProvider = async (name:string) => { await fetch("http://127.0.0.1:9876/providers/"+encodeURIComponent(name),{method:"DELETE"});loadProviders(); };
    const editProvider = (p: any) => {
        setEditingProviderName(p.name || "");
        setNewProviderName(p.name || "");
        setProvider(p.provider || "openai");
        setBaseUrl(p.base_url || "");
        setModel(p.model || "gpt-4o");
        setMaxContext(p.max_context_tokens || 24000);
        setApiKey(p.api_key || "");
        setActiveTab("llm");
    };
    const switchProvider = async (p:any) => {
        setProvider(p.provider||"openai");setBaseUrl(p.base_url||"");
        if (p.api_key) setApiKey(p.api_key);
        // Update backend config so /models returns correct provider
        await fetch("http://127.0.0.1:9876/settings", {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({
                provider: p.provider||"openai",
                model: p.model||"",
                api_key: p.api_key||"",
                base_url: p.base_url||"",
            }),
        }).catch(() => {});
        // Fetch remote models for this provider
        const url = p.base_url||"";
        if (url && url !== "https://api.openai.com/v1") {
            setFetchingModels(true);
            try {
                let fetchUrl = url.endsWith("/") ? url + "models" : url + "/models";
                if (!fetchUrl.includes("/v1/")) fetchUrl = url.endsWith("/") ? url + "v1/models" : url + "/v1/models";
                const finalUrl = url.endsWith("/v1") ? url + "/models" : fetchUrl;
                const res = await fetch(finalUrl, { headers: { "Authorization": "Bearer "+(p.api_key||apiKey||"") } });
                if (res.ok) {
                    const data = await res.json();
                    if (data?.data && Array.isArray(data.data)) {
                        const ids = data.data.map((m: any) => m.id);
                        setCustomModels(ids);
                        if (ids.length > 0 && !ids.includes(p.model)) setModel(ids[0]);
                        else if (ids.length > 0) setModel(p.model);
                        setFetchingModels(false);
                        return;
                    }
                }
            } catch {}
            setFetchingModels(false);
        }
        setModel(p.model||"gpt-4o");
    };

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

    const [activeTab, setActiveTab] = useState<"providers" | "general" | "llm" | "vision" | "editor" | "terminal" | "shortcuts">("llm");
    const [shortcutBindings, setShortcutBindings] = useState<
        Array<{ id: string; label: string; display: string; defaultDisplay: string; isCustom: boolean }>
    >([]);

    const tabs: Array<{ id: typeof activeTab; label: string }> = [
        { id: "providers", label: "服务商" },
        { id: "general", label: "设置" },
        { id: "llm", label: "LLM API" },
        { id: "vision", label: "视觉模型" },
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

    useEffect(() => { fetchModels(); loadProviders(); }, [fetchModels]);
    useEffect(() => {
        if (baseUrl && baseUrl !== "https://api.openai.com/v1") {
            fetchRemoteModels();
        }
    }, [provider, baseUrl]);

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
            if (newProviderName.trim() && apiKey.trim()) {
                await saveProvider();
            }
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
                            <div>
                                <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>服务商名称</label>
                                <input value={newProviderName} onChange={(e: any) => setNewProviderName(e.target.value)}
                                    placeholder="服务商名称"
                                    style={{ ...inputStyle, fontSize: 13, fontWeight: 600 }} />
                                {editingProviderName && (
                                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
                                        <span style={{ fontSize: 10, color: colors.accent }}>正在编辑：{editingProviderName}</span>
                                        <button onClick={() => { setEditingProviderName(null); setNewProviderName(""); setApiKey(""); }}
                                            style={{ background: "transparent", border: "none", color: colors.textSecondary, fontSize: 10, cursor: "pointer" }}>
                                            取消编辑
                                        </button>
                                    </div>
                                )}
                            </div>
                            <div>
                                <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>API Base URL</label>
                                <input type="text" value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
                                    placeholder="https://api.deepseek.com" style={inputStyle} />
                            </div>
                            <div>
                                <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>API Key</label>
                                <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
                                    placeholder="sk-..." style={inputStyle} />
                            </div>
                            <div>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                                    <label style={{ fontSize: 11, color: colors.textSecondary }}>模型</label>
                                    <button onClick={fetchRemoteModels} disabled={fetchingModels || !baseUrl}
                                        style={{ background: "transparent", border: "none", color: colors.accent, fontSize: 11, cursor: baseUrl ? "pointer" : "not-allowed", opacity: baseUrl ? 1 : 0.5 }}>
                                        {fetchingModels ? "拉取中..." : "🔄 拉取模型列表"}
                                    </button>
                                </div>
                                <select value={model} onChange={e => setModel(e.target.value)} style={inputStyle}>
                                    {customModels.map(m => <option key={m} value={m}>{m}</option>)}
                                    {customModels.length === 0 && <option value={model}>{model || "(先拉取模型)"}</option>}
                                </select>
                            </div>

                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                                <div>
                                    <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>上下文窗口 (tokens)</label>
                                    <input type="number" value={maxContext} min={1000} max={500000} step={1000}
                                        onChange={e => setMaxContext(parseInt(e.target.value) || 24000)} style={inputStyle} />
                                </div>
                                <div>
                                    <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 4 }}>Temperature</label>
                                    <input type="number" value={temperature} min={0} max={2} step={0.05}
                                        onChange={e => setTemperature(parseFloat(e.target.value) || 0.3)} style={inputStyle} />
                                </div>
                            </div>

                            <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                                <button onClick={handleSave} disabled={saving}
                                    style={{ flex: 1, padding: "8px", background: colors.accent, color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 12, opacity: saving ? 0.7 : 1 }}>
                                    {saving ? t("saving") : (editingProviderName ? "保存服务商修改" : t("saveSettings"))}
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
                                        ? `成功：${testResult.response}`
                                        : `错误：${testResult.error || t("unknownError")}`
                                    }
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === "general" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                            <div>
                                <label style={{ fontSize: 11, color: colors.textSecondary, display: "block", marginBottom: 6 }}>
                                    主题
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
                                主模型无法识别图片时，自动切换到支持视觉的备用模型。
                                默认复用你的主 LLM API Key。
                            </div>

                            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: colors.text }}>
                                <input type="checkbox" checked={visionEnabled} onChange={e => setVisionEnabled(e.target.checked)} />
                                启用视觉备用模型
                            </label>

                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                <label style={{ fontSize: 11, color: colors.textSecondary }}>视觉模型</label>
                                <select
                                    value={visionModel}
                                    onChange={e => setVisionModel(e.target.value)}
                                    style={{
                                        background: colors.bg, color: colors.text, border: `1px solid ${colors.border}`,
                                        borderRadius: 6, padding: "6px 10px", fontSize: 12, outline: "none",
                                    }}
                                >
                                    <option value={visionModel}>{visionModel}</option>
                                </select>
                            </div>

                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                <label style={{ fontSize: 11, color: colors.textSecondary }}>
                                    视觉 API Key <span style={{ color: colors.textSecondary || colors.textSecondary }}>(可选，默认复用主 LLM Key)</span>
                                </label>
                                <input
                                    type="password"
                                    value={visionApiKey}
                                    onChange={e => setVisionApiKey(e.target.value)}
                                    placeholder={t("visionApiKeyPlaceholder")}
                                    style={{
                                        background: colors.bg, color: colors.text, border: `1px solid ${colors.border}`,
                                        borderRadius: 6, padding: "6px 10px", fontSize: 12, outline: "none",
                                    }}
                                />
                            </div>

                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                <label style={{ fontSize: 11, color: colors.textSecondary }}>
                                    视觉 Base URL <span style={{ color: colors.textSecondary || colors.textSecondary }}>(可选)</span>
                                </label>
                                <input
                                    type="text"
                                    value={visionBaseUrl}
                                    onChange={e => setVisionBaseUrl(e.target.value)}
                                    placeholder={t("visionBaseUrlPlaceholder")}
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
                            <label style={{ fontSize: 11, color: colors.textSecondary }}>字号：{fontSize}px</label>
                            <input type="range" min={10} max={30} value={fontSize} onChange={e => setEditorFontSize(parseInt(e.target.value))} />
                            <label style={{ fontSize: 11, color: colors.textSecondary }}>缩进：{tabSize}</label>
                            <input type="range" min={2} max={8} value={tabSize} onChange={e => setEditorTabSize(parseInt(e.target.value))} />
                            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: colors.text }}>
                                <input type="checkbox" checked={wordWrap} onChange={e => setEditorWordWrap(e.target.checked)} /> 自动换行
                            </label>
                            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: colors.text }}>
                                <input type="checkbox" checked={minimap} onChange={e => setEditorMinimap(e.target.checked)} /> 代码缩略图
                            </label>
                        </div>
                    )}

                    {activeTab === "terminal" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                            <label style={{ fontSize: 11, color: colors.textSecondary }}>终端字号：{terminalFontSize}px</label>
                            <input type="range" min={10} max={24} value={terminalFontSize} onChange={e => setTerminalFontSize(parseInt(e.target.value))} />
                        </div>
                    )}

                    {activeTab === "shortcuts" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                            <div style={{
                                fontSize: 10, color: colors.textSecondary,
                                marginBottom: 4, fontStyle: "italic",
                            }}>
                                点击快捷键项重新绑定，然后按下想使用的组合键。
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
                                                重置
                                            </button>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {activeTab === "providers" && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 4 }}>
                                点击服务商切换配置 · 点“编辑”可修改后保存
                            </div>
                            {savedProviders.map((p: any) => (
                                <div key={p.name} onClick={() => switchProvider(p)} style={{
                                    display: "flex", alignItems: "center", justifyContent: "space-between",
                                    padding: "10px 12px", borderRadius: 8, cursor: "pointer",
                                    border: `1px solid ${colors.border}`,
                                    backgroundColor: (p.provider===provider && p.base_url===baseUrl) ? (colors.accent+"15") : colors.bgSecondary,
                                }}>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ fontWeight: 600, fontSize: 13 }}>{p.name}</div>
                                        <div style={{ fontSize: 10, color: colors.textSecondary, marginTop: 2 }}>
                                            {p.provider} · {p.model} · {p.base_url}
                                        </div>
                                    </div>
                                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                                        {(p.provider===provider && p.base_url===baseUrl) && (
                                            <span style={{ fontSize: 9, color: "#22c55e", fontWeight: 600 }}>当前</span>
                                        )}
                                        <button onClick={(e: any) => { e.stopPropagation(); editProvider(p); }}
                                            style={{ padding: "3px 8px", fontSize: 10, borderRadius: 4, border: "none", background: colors.accent, color: "#fff", cursor: "pointer" }}>
                                            编辑
                                        </button>
                                        <button onClick={(e: any) => { e.stopPropagation(); deleteProvider(p.name); }}
                                            style={{ padding: "3px 8px", fontSize: 10, borderRadius: 4, border: "none", background: "#ef4444", color: "#fff", cursor: "pointer" }}>
                                            删除
                                        </button>
                                    </div>
                                </div>
                            ))}
                            {savedProviders.length === 0 && (
                                <div style={{ color: colors.textSecondary, fontSize: 12, textAlign: "center", padding: 20 }}>
                                    还没有保存的服务商<br/>在 LLM API 页配置后保存
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
