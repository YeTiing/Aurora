
import React, { useState } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";
import { applySkinBackgrounds, applyTheme, getThemeByName, getStoredThemeName } from "../../theme";

interface SkinBrowserProps {
    onClose: () => void;
}

const BUILTIN_SKINS = [
    { name: "aurora-dark", label: "Aurora Dark", bg_preview: "#09090b", surface_preview: "#18181b", text_preview: "#f4f4f5", accent: "#3b82f6", accent_color_name: "blue", is_builtin: true },
    { name: "aurora-light", label: "Aurora Light", bg_preview: "#ffffff", surface_preview: "#f4f4f5", text_preview: "#09090b", accent: "#2563eb", accent_color_name: "blue", is_builtin: true },
    { name: "ocean-blue", label: "Ocean Blue", bg_preview: "#0f172a", surface_preview: "#1e293b", text_preview: "#f8fafc", accent: "#0ea5e9", accent_color_name: "blue", is_builtin: true },
    { name: "midnight-purple", label: "Midnight Purple", bg_preview: "#171026", surface_preview: "#281b40", text_preview: "#fdfcff", accent: "#a855f7", accent_color_name: "purple", is_builtin: true },
    { name: "forest-green", label: "Forest Green", bg_preview: "#05160d", surface_preview: "#0c2817", text_preview: "#f1fbf4", accent: "#22c55e", accent_color_name: "green", is_builtin: true },
    { name: "sunset-orange", label: "Sunset Orange", bg_preview: "#1e100a", surface_preview: "#331c12", text_preview: "#fff6f0", accent: "#f97316", accent_color_name: "orange", is_builtin: true },
    { name: "rose-dawn", label: "Rose Dawn", bg_preview: "#201014", surface_preview: "#361b23", text_preview: "#fff4f6", accent: "#f43f5e", accent_color_name: "rose" },
    { name: "monochrome", label: "Monochrome", bg_preview: "#000000", surface_preview: "#111111", text_preview: "#ffffff", accent: "#ffffff", accent_color_name: "white" },
    { name: "ayanami-pink", label: "Ayanami Pink", bg_preview: "#20111a", surface_preview: "#351c2c", text_preview: "#fce7f3", accent: "#ec4899", accent_color_name: "pink" }
];

export function SkinBrowser({ onClose }: SkinBrowserProps) {
    const colors = useStore((s) => s.themeColors);
    const [activeSkin, setActiveSkin] = useState(() => getStoredThemeName());
    
    // 超级预览隐身与停靠状态
    const [ghost, setGhost] = useState(false);
    const [panelPos, setPanelPos] = useState<"left" | "center" | "right">("center");

    const handleApply = async (skinName: string) => {
        const theme = getThemeByName(skinName);
        applyTheme(theme);
        applySkinBackgrounds();
        useStore.setState({
            theme: theme.name === "aurora-light" || theme.name === "github-light" || theme.name === "sakura-petals" ? "light" : "dark",
            themeColors: { ...theme.colors, bgSecondary: theme.colors.surface, accentHover: theme.colors.accent } as any,
        });
        setActiveSkin(theme.name);
        try {
            await fetch(`http://127.0.0.1:9876/skins/${encodeURIComponent(skinName)}/apply`, { method: "POST" });
        } catch {}
    };

    const saveImage = (key: string, value: string) => {
        localStorage.setItem(key, value);
        applySkinBackgrounds();
    };

    const clearImage = (key: string) => {
        localStorage.removeItem(key);
        applySkinBackgrounds();
    };

    const pickImage = (key: string) => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.onchange = (e) => {
            const file = (e.target as HTMLInputElement).files?.[0] as any;
            const localPath = file?.path;
            if (localPath) {
                saveImage(key, localPath);
                return;
            }
            if (file) {
                const reader = new FileReader();
                reader.onload = () => saveImage(key, String(reader.result || ""));
                reader.readAsDataURL(file);
            }
        };
        input.click();
    };

    // 所有滑动条共通的超级隐身挂载钩子
    const ghostProps = {
        onPointerDown: () => setGhost(true),
        onPointerUp: () => setGhost(false),
        onPointerLeave: () => setGhost(false) // 防止滑出范围卡死隐身
    };

    return (
        <div style={{
            position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
            zIndex: 1000, display: "flex", alignItems: "center",
            justifyContent: panelPos === "left" ? "flex-start" : panelPos === "right" ? "flex-end" : "center",
            padding: panelPos === "center" ? 0 : "0 20px"
        }}>
            {/* 深色暗背景墙（当隐身拖拽时，墙面直接消失，完全通透底板） */}
            <div style={{
                position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
                backgroundColor: "rgba(0,0,0,0.5)", 
                backdropFilter: ghost ? "none" : "blur(2px)",
                opacity: ghost ? 0 : 1, transition: "all 0.2s ease"
            }} onClick={onClose} />

            {/* 控制总面板本体 */}
            <div style={{
                position: "relative", 
                width: panelPos === "center" ? "90vw" : "450px", 
                maxWidth: 800, maxHeight: "85vh",
                display: "flex", flexDirection: "column",
                backgroundColor: colors.surface, borderRadius: 12,
                border: `1px solid ${colors.border}`,
                boxShadow: "0 25px 50px -12px rgba(0,0,0,0.7)",
                // 拖拽时完全变成半透明鬼魂面板！
                opacity: ghost ? 0.2 : 1, scale: ghost ? 0.98 : 1,
                transition: "all 0.2s cubic-bezier(0.25, 0.8, 0.25, 1)"
            }}>
                <div className="skin-browser-header" style={{
                    padding: "16px 20px", display: "flex", justifyContent: "space-between", alignItems: "center",
                    borderBottom: `1px solid ${colors.border}`, backgroundColor: "rgba(0,0,0,0.2)", borderRadius: "12px 12px 0 0"
                }}>
                    <h2 style={{ fontSize: "16px", fontWeight: 600, margin: 0, color: colors.text }}>🎛️ 皮肤与位置定制</h2>
                    <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                        {/* 位置停靠开关组件 */}
                        <div style={{ display: "flex", gap: "2px", background: "rgba(0,0,0,0.3)", borderRadius: "6px", padding: "4px" }}>
                            <button onClick={() => setPanelPos("left")} style={{ background: panelPos==="left" ? "var(--aurora-accent)" : "transparent", border: "none", color: "#fff", cursor: "pointer", borderRadius: "4px", padding: "2px 8px", fontSize: "12px" }}>居左看</button>
                            <button onClick={() => setPanelPos("center")} style={{ background: panelPos==="center" ? "var(--aurora-accent)" : "transparent", border: "none", color: "#fff", cursor: "pointer", borderRadius: "4px", padding: "2px 8px", fontSize: "12px" }}>居中</button>
                            <button onClick={() => setPanelPos("right")} style={{ background: panelPos==="right" ? "var(--aurora-accent)" : "transparent", border: "none", color: "#fff", cursor: "pointer", borderRadius: "4px", padding: "2px 8px", fontSize: "12px" }}>居右看</button>
                        </div>
                        <button className="aurora-right-close" onClick={onClose} title={t("closePanel")}>✕</button>
                    </div>
                </div>
                
                {/* 滚动核心内容层 */}
                <div className="skin-scroll-box" style={{ flex: 1, overflowY: "auto", padding: "20px", minHeight: 0 }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                    
                        {/* ===================== 全局壁纸 ===================== */}
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <h3 style={{ fontSize: "14px", color: "var(--aurora-accent)" }}>🌌 全局总壁纸 (大背景)</h3>
                        </div>
                        <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                            <button className="toolbar-btn" style={{ background: 'var(--dark-border)', color: 'var(--dark-text)', padding: '0 12px', minWidth: '80px' }}
                            onClick={() => pickImage('aurora_anime_bg')}>📁 本地图</button>
                            <input type="text" placeholder="C:\ 或 https://..." 
                            style={{ flex: 1, padding: '4px 8px', background: 'var(--dark-bg)', border: '1px solid var(--dark-border)', color: 'var(--dark-text)', borderRadius: '6px' }}
                            defaultValue={localStorage.getItem('aurora_anime_bg') || ""}
                            onChange={(e) => { saveImage('aurora_anime_bg', e.target.value); }}
                            onBlur={() => { applySkinBackgrounds(); }}/>
                            <button className="toolbar-btn" style={{ background: '#7f1d1d', color: '#fff', padding: '0 12px' }}
                            onClick={() => { clearImage('aurora_anime_bg'); }}>✕ 清除</button>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '10px', background: 'rgba(0,0,0,0.2)', padding: '10px', borderRadius: '8px' }}>
                            <span style={{ fontSize: "12px", color: "var(--dark-text)" }}>全局壁纸 定位微调:</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '11px', width: '45px', whiteSpace: 'nowrap', color: '#999' }}>水平 X</span>
                                <input type="range" min="0" max="100" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_pos_x_Main') || "50"} style={{ flex: 1 }}
                                onChange={(e) => {
                                    localStorage.setItem('aurora_pos_x_Main', e.target.value); 
                                    const y = localStorage.getItem('aurora_pos_y_Main') || "50";
                                    const pos = `${e.target.value}% ${y}%`; 
                                    localStorage.setItem('aurora_pos_Main', pos); 
                                    document.documentElement.style.setProperty("--pos-main", pos);
                                }}/>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '11px', width: '45px', whiteSpace: 'nowrap', color: '#999' }}>垂直 Y</span>
                                <input type="range" min="0" max="100" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_pos_y_Main') || "50"} style={{ flex: 1 }}
                                onChange={(e) => {
                                    localStorage.setItem('aurora_pos_y_Main', e.target.value); 
                                    const x = localStorage.getItem('aurora_pos_x_Main') || "50";
                                    const pos = `${x}% ${e.target.value}%`; 
                                    localStorage.setItem('aurora_pos_Main', pos); 
                                    document.documentElement.style.setProperty("--pos-main", pos);
                                }}/>
                            </div>
                        </div>
                        <hr style={{ borderColor: 'var(--dark-border)', margin: '15px 0' }} />
                        
                        {/* ===================== 左侧栏 ===================== */}
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <h3 style={{ fontSize: "14px", color: "var(--aurora-accent)" }}>📜 左侧会话栏 透明度</h3>
                            <input type="range" min="0" max="1" step="0.05" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_opacity_L') || "0.6"} style={{ width: '100px', accentColor: "var(--aurora-accent)" }}
                                onChange={(e) => { localStorage.setItem('aurora_opacity_L', e.target.value); document.documentElement.style.setProperty("--opacity-l", e.target.value); }}/>
                        </div>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '10px' }}>
                            <h3 style={{ fontSize: "14px", color: "var(--aurora-accent)" }}>左侧 模糊度 (消除发虚)</h3>
                            <input type="range" min="0" max="30" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_blur_L') || "10"} style={{ width: '100px', accentColor: "var(--aurora-accent)" }}
                                onChange={(e) => { localStorage.setItem('aurora_blur_L', e.target.value); document.documentElement.style.setProperty("--blur-l", e.target.value + "px"); }}/>
                        </div>
                        <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                            <button className="toolbar-btn" style={{ background: 'var(--dark-border)', color: 'var(--dark-text)', padding: '0 12px', minWidth: '60px' }}
                            onClick={() => pickImage('aurora_bg_L')}>📁 图</button>
                            <input type="text" placeholder="C:\ 或 https://..."
                            style={{ flex: 1, padding: '4px 8px', background: 'var(--dark-bg)', border: '1px solid var(--dark-border)', color: 'var(--dark-text)', borderRadius: '6px' }}
                            defaultValue={localStorage.getItem('aurora_bg_L') || ""}
                            onChange={(e) => { saveImage('aurora_bg_L', e.target.value); }}
                            onBlur={() => { applySkinBackgrounds(); }}/>
                            <button className="toolbar-btn" style={{ background: '#7f1d1d', color: '#fff', padding: '0 12px' }}
                            onClick={() => { clearImage('aurora_bg_L'); }}>✕ 重置</button>
                        </div>
                        {/* 左侧XYZ定位调整（已修复极窄断行） */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '15px', background: 'rgba(0,0,0,0.2)', padding: '10px', borderRadius: '8px' }}>
                            <span style={{ fontSize: "12px", color: "var(--dark-text)" }}>左侧 背景图定位调整:</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '11px', width: '45px', whiteSpace: 'nowrap', color: '#999' }}>水平 X</span>
                                <input type="range" min="0" max="100" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_pos_x_L') || "50"} style={{ flex: 1 }}
                                onChange={(e) => {
                                    localStorage.setItem('aurora_pos_x_L', e.target.value); const y = localStorage.getItem('aurora_pos_y_L') || "50";
                                    const pos = `${e.target.value}% ${y}%`; localStorage.setItem('aurora_pos_L', pos); document.documentElement.style.setProperty("--pos-l", pos);
                                }}/>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '11px', width: '45px', whiteSpace: 'nowrap', color: '#999' }}>垂直 Y</span>
                                <input type="range" min="0" max="100" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_pos_y_L') || "50"} style={{ flex: 1 }}
                                onChange={(e) => {
                                    localStorage.setItem('aurora_pos_y_L', e.target.value); const x = localStorage.getItem('aurora_pos_x_L') || "50";
                                    const pos = `${x}% ${e.target.value}%`; localStorage.setItem('aurora_pos_L', pos); document.documentElement.style.setProperty("--pos-l", pos);
                                }}/>
                            </div>
                        </div>
                        <hr style={{ borderColor: 'var(--dark-border)', margin: '15px 0' }} />

                        {/* ===================== 中控面板 ===================== */}
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <h3 style={{ fontSize: "14px", color: "var(--aurora-accent)" }}>💬 中控聊天台 透明度</h3>
                            <input type="range" min="0" max="1" step="0.05" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_opacity_C') || "0.6"} style={{ width: '100px', accentColor: "var(--aurora-accent)" }}
                                onChange={(e) => { localStorage.setItem('aurora_opacity_C', e.target.value); document.documentElement.style.setProperty("--opacity-c", e.target.value); }}/>
                        </div>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '10px' }}>
                            <h3 style={{ fontSize: "14px", color: "var(--aurora-accent)" }}>中控 模糊度 (消除发虚)</h3>
                            <input type="range" min="0" max="30" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_blur_C') || "10"} style={{ width: '100px', accentColor: "var(--aurora-accent)" }}
                                onChange={(e) => { localStorage.setItem('aurora_blur_C', e.target.value); document.documentElement.style.setProperty("--blur-c", e.target.value + "px"); }}/>
                        </div>
                        <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                            <button className="toolbar-btn" style={{ background: 'var(--dark-border)', color: 'var(--dark-text)', padding: '0 12px', minWidth: '60px' }}
                            onClick={() => pickImage('aurora_bg_C')}>📁 图</button>
                            <input type="text" placeholder="C:\ 或 https://..."
                            style={{ flex: 1, padding: '4px 8px', background: 'var(--dark-bg)', border: '1px solid var(--dark-border)', color: 'var(--dark-text)', borderRadius: '6px' }}
                            defaultValue={localStorage.getItem('aurora_bg_C') || ""}
                            onChange={(e) => { saveImage('aurora_bg_C', e.target.value); }}
                            onBlur={() => { applySkinBackgrounds(); }}/>
                            <button className="toolbar-btn" style={{ background: '#7f1d1d', color: '#fff', padding: '0 12px' }}
                            onClick={() => { clearImage('aurora_bg_C'); }}>✕ 重置</button>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '15px', background: 'rgba(0,0,0,0.2)', padding: '10px', borderRadius: '8px' }}>
                            <span style={{ fontSize: "12px", color: "var(--dark-text)" }}>中控 背景图定位调整:</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '11px', width: '45px', whiteSpace: 'nowrap', color: '#999' }}>水平 X</span>
                                <input type="range" min="0" max="100" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_pos_x_C') || "50"} style={{ flex: 1 }}
                                onChange={(e) => {
                                    localStorage.setItem('aurora_pos_x_C', e.target.value); const y = localStorage.getItem('aurora_pos_y_C') || "50";
                                    const pos = `${e.target.value}% ${y}%`; localStorage.setItem('aurora_pos_C', pos); document.documentElement.style.setProperty("--pos-c", pos);
                                }}/>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '11px', width: '45px', whiteSpace: 'nowrap', color: '#999' }}>垂直 Y</span>
                                <input type="range" min="0" max="100" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_pos_y_C') || "50"} style={{ flex: 1 }}
                                onChange={(e) => {
                                    localStorage.setItem('aurora_pos_y_C', e.target.value); const x = localStorage.getItem('aurora_pos_x_C') || "50";
                                    const pos = `${x}% ${e.target.value}%`; localStorage.setItem('aurora_pos_C', pos); document.documentElement.style.setProperty("--pos-c", pos);
                                }}/>
                            </div>
                        </div>
                        <hr style={{ borderColor: 'var(--dark-border)', margin: '15px 0' }} />

                        {/* ===================== 右侧面板 ===================== */}
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <h3 style={{ fontSize: "14px", color: "var(--aurora-accent)" }}>📁 右侧文件栏 透明度</h3>
                            <input type="range" min="0" max="1" step="0.05" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_opacity_R') || "0.6"} style={{ width: '100px', accentColor: "var(--aurora-accent)" }}
                                onChange={(e) => { localStorage.setItem('aurora_opacity_R', e.target.value); document.documentElement.style.setProperty("--opacity-r", e.target.value); }}/>
                        </div>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '10px' }}>
                            <h3 style={{ fontSize: "14px", color: "var(--aurora-accent)" }}>右侧 模糊度 (消除发虚)</h3>
                            <input type="range" min="0" max="30" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_blur_R') || "10"} style={{ width: '100px', accentColor: "var(--aurora-accent)" }}
                                onChange={(e) => { localStorage.setItem('aurora_blur_R', e.target.value); document.documentElement.style.setProperty("--blur-r", e.target.value + "px"); }}/>
                        </div>
                        <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                            <button className="toolbar-btn" style={{ background: 'var(--dark-border)', color: 'var(--dark-text)', padding: '0 12px', minWidth: '60px' }}
                            onClick={() => pickImage('aurora_bg_R')}>📁 图</button>
                            <input type="text" placeholder="C:\ 或 https://..."
                            style={{ flex: 1, padding: '4px 8px', background: 'var(--dark-bg)', border: '1px solid var(--dark-border)', color: 'var(--dark-text)', borderRadius: '6px' }}
                            defaultValue={localStorage.getItem('aurora_bg_R') || ""}
                            onChange={(e) => { saveImage('aurora_bg_R', e.target.value); }}
                            onBlur={() => { applySkinBackgrounds(); }}/>
                            <button className="toolbar-btn" style={{ background: '#7f1d1d', color: '#fff', padding: '0 12px' }}
                            onClick={() => { clearImage('aurora_bg_R'); }}>✕ 重置</button>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '15px', background: 'rgba(0,0,0,0.2)', padding: '10px', borderRadius: '8px' }}>
                            <span style={{ fontSize: "12px", color: "var(--dark-text)" }}>右侧 背景图定位调整:</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '11px', width: '45px', whiteSpace: 'nowrap', color: '#999' }}>水平 X</span>
                                <input type="range" min="0" max="100" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_pos_x_R') || "50"} style={{ flex: 1 }}
                                onChange={(e) => {
                                    localStorage.setItem('aurora_pos_x_R', e.target.value); const y = localStorage.getItem('aurora_pos_y_R') || "50";
                                    const pos = `${e.target.value}% ${y}%`; localStorage.setItem('aurora_pos_R', pos); document.documentElement.style.setProperty("--pos-r", pos);
                                }}/>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '11px', width: '45px', whiteSpace: 'nowrap', color: '#999' }}>垂直 Y</span>
                                <input type="range" min="0" max="100" step="1" {...ghostProps}
                                defaultValue={localStorage.getItem('aurora_pos_y_R') || "50"} style={{ flex: 1 }}
                                onChange={(e) => {
                                    localStorage.setItem('aurora_pos_y_R', e.target.value); const x = localStorage.getItem('aurora_pos_x_R') || "50";
                                    const pos = `${x}% ${e.target.value}%`; localStorage.setItem('aurora_pos_R', pos); document.documentElement.style.setProperty("--pos-r", pos);
                                }}/>
                            </div>
                        </div>
                        <hr style={{ borderColor: 'var(--dark-border)', margin: '15px 0' }} />

                        {/* ===================== 主题卡片渲染 ===================== */}
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "16px" }}>
                            {BUILTIN_SKINS.map((skin) => {
                                const isActive = activeSkin === skin.name;
                                return (
                                    <div key={skin.name} className={`skin-card ${isActive ? 'active' : ''}`}
                                        style={{ padding: "16px", borderRadius: "10px", border: `2px solid ${isActive ? skin.accent : colors.border}`, backgroundColor: "rgba(0,0,0,0.3)", cursor: "pointer", transition: "all 0.15s ease" }}
                                        onClick={() => handleApply(skin.name)} >
                                        <div className="skin-card-header">
                                            <div className="skin-accent-dot" style={{ backgroundColor: skin.accent, width: 12, height: 12, borderRadius: "50%", display: "inline-block", marginRight: "8px", verticalAlign: "middle" }} />
                                            <span style={{ fontWeight: 600, fontSize: "14px", verticalAlign: "middle" }}>{skin.label}</span>
                                            {isActive && <span style={{ color: skin.accent, fontSize: "11px", float: "right" }}>使用中</span>}
                                        </div>
                                        <div className="skin-card-swatches" style={{ display: "flex", gap: "6px", marginTop: "12px" }}>
                                            <span className="swatch" style={{ backgroundColor: skin.bg_preview, width: 22, height: 22, borderRadius: "6px", border: `1px solid ${colors.border}` }} />
                                            <span className="swatch" style={{ backgroundColor: skin.surface_preview, width: 22, height: 22, borderRadius: "6px", border: `1px solid ${colors.border}` }} />
                                            <span className="swatch" style={{ backgroundColor: skin.accent, width: 22, height: 22, borderRadius: "6px" }} />
                                            <span className="swatch" style={{ backgroundColor: skin.text_preview, width: 22, height: 22, borderRadius: "6px", border: `1px solid ${colors.border}` }} />
                                        </div>
                                        <div style={{ fontSize: "11px", color: colors.textSecondary, marginTop: "8px" }}>
                                            {skin.accent_color_name}{skin.is_builtin ? " . built-in" : ""}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
