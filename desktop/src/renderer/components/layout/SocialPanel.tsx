import React from "react";

const SOCIALS = [
    {
        name: "Bilibili",
        icon: "▶",
        url: "https://www.bilibili.com",
        color: "#fb7299",
        desc: "中文视频平台"
    },
    {
        name: "Douyin",
        icon: "♫",
        url: "https://www.douyin.com",
        color: "#000000",
        desc: "短视频平台"
    },
    {
        name: "Xiaohongshu",
        icon: "📕",
        url: "https://www.xiaohongshu.com",
        color: "#fe2c55",
        desc: "生活方式平台"
    },
    {
        name: "GitHub",
        icon: "⬡",
        url: "https://github.com/YeTiing/Aurora",
        color: "#6e40c9",
        desc: "Aurora 源代码"
    },
    {
        name: "StackOverflow",
        icon: "📚",
        url: "https://stackoverflow.com",
        color: "#f48024",
        desc: "开发者问答"
    },
    {
        name: "npm",
        icon: "📦",
        url: "https://www.npmjs.com",
        color: "#cb3837",
        desc: "Node.js 包"
    },
    {
        name: "PyPI",
        icon: "🐍",
        url: "https://pypi.org",
        color: "#3776ab",
        desc: "Python 包"
    },
    {
        name: "MDN",
        icon: "📖",
        url: "https://developer.mozilla.org",
        color: "#000000",
        desc: "Web 文档"
    },
];

export function SocialPanel({ onClose }: { onClose: () => void }) {
    const openLink = (url: string) => {
        if (window.auroraAPI?.browser?.open) {
            window.auroraAPI?.browser?.open(url);
        } else {
            window.open(url, "_blank");
        }
    };

    return (
        <div style={{
            display: "flex", flexDirection: "column", height: "100%",
            background: "var(--bg-panel, #1a1b26)", color: "var(--text, #c0caf5)",
            fontFamily: "var(--font-mono, monospace)",
        }}>
            {/* Header */}
            <div style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "12px 16px", borderBottom: "1px solid var(--border, #252636)",
            }}>
                <div>
                    <div style={{ fontSize: 16, fontWeight: 700 }}>🔗 资源导航</div>
                    <div style={{ fontSize: 11, color: "var(--text-dim, #565f89)", marginTop: 2 }}>
                        快速打开开发和常用资源
                    </div>
                </div>
                <button onClick={onClose}
                    style={{
                        width: 28, height: 28, borderRadius: 6,
                        border: "none", cursor: "pointer", fontSize: 16,
                        background: "var(--bg-button, #252636)",
                        color: "var(--text-dim, #565f89)",
                    }}
                >✕</button>
            </div>

            {/* Social Grid */}
            <div style={{
                display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                gap: 10, padding: 16, overflow: "auto",
            }}>
                {SOCIALS.map((s) => (
                    <button
                        key={s.name}
                        onClick={() => openLink(s.url)}
                        style={{
                            display: "flex", flexDirection: "column", alignItems: "center",
                            gap: 8, padding: "16px 12px", borderRadius: 10,
                            border: "1px solid var(--border, #252636)",
                            background: "var(--bg-button, #252636)",
                            cursor: "pointer", transition: "all 0.2s",
                        }}
                        onMouseEnter={(e) => {
                            e.currentTarget.style.borderColor = s.color;
                            e.currentTarget.style.transform = "translateY(-2px)";
                        }}
                        onMouseLeave={(e) => {
                            e.currentTarget.style.borderColor = "var(--border, #252636)";
                            e.currentTarget.style.transform = "translateY(0)";
                        }}
                    >
                        <div style={{
                            width: 48, height: 48, borderRadius: 12,
                            display: "flex", alignItems: "center", justifyContent: "center",
                            fontSize: 24, background: s.color + "22",
                        }}>
                            {s.icon}
                        </div>
                        <div style={{ fontWeight: 600, fontSize: 13 }}>{s.name}</div>
                        <div style={{ fontSize: 10, color: "var(--text-dim, #565f89)" }}>
                            {s.desc}
                        </div>
                    </button>
                ))}
            </div>

            {/* Footer */}
            <div style={{
                padding: "10px 16px", borderTop: "1px solid var(--border, #252636)",
                fontSize: 11, color: "var(--text-faint, #3b4261)", textAlign: "center",
            }}>
                点击后在内置浏览器中打开 · Aurora v0.2.0
            </div>
        </div>
    );
}
