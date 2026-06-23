// InlineCodeComment — Codex ::code-comment{} 渲染器
// 在编辑器中标记和渲染内联批注
import React, { useState, useCallback } from "react";

export interface CodeComment {
    title: string;
    body: string;
    file: string;
    start: number;
    end?: number;
    priority?: number;
}

interface InlineCodeCommentProps {
    comments: CodeComment[];
    onDismiss?: (index: number) => void;
    onNavigate?: (file: string, line: number) => void;
}

// 解析 ::code-comment{...} 指令
export function parseCodeCommentDirective(text: string): CodeComment[] {
    const regex = /::code-comment\{([^}]+)\}/g;
    const comments: CodeComment[] = [];
    let match;

    while ((match = regex.exec(text)) !== null) {
        const attrs: Record<string, string> = {};
        // 解析 key="value" 属性
        const attrRegex = /(\w+)\s*=\s*"([^"]*)"/g;
        let attrMatch;
        while ((attrMatch = attrRegex.exec(match[1])) !== null) {
            attrs[attrMatch[1]] = attrMatch[2];
        }

        if (attrs.title && attrs.body && attrs.file) {
            comments.push({
                title: attrs.title,
                body: attrs.body,
                file: attrs.file,
                start: parseInt(attrs.start || "1"),
                end: attrs.end ? parseInt(attrs.end) : undefined,
                priority: attrs.priority ? parseInt(attrs.priority) : undefined,
            });
        }
    }
    return comments;
}

export const InlineCodeComment: React.FC<InlineCodeCommentProps> = ({
    comments,
    onDismiss,
    onNavigate,
}) => {
    const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

    const priorityColor = (p: number | undefined) => {
        switch (p) {
            case 0: return "#0969da";
            case 1: return "#9a6700";
            case 2: return "#cf222e";
            default: return "#656d76";
        }
    };

    if (comments.length === 0) return null;

    return (
        <div className="inline-comments-container" style={{ padding: "8px 0" }}>
            {comments.map((c, i) => (
                <div
                    key={i}
                    className="inline-comment-item"
                    style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 8,
                        padding: "6px 12px",
                        borderLeft: `3px solid ${priorityColor(c.priority)}`,
                        backgroundColor: selectedIndex === i ? "rgba(88,166,255,0.08)" : "transparent",
                        cursor: "pointer",
                        transition: "background-color 0.15s",
                    }}
                    onClick={() => {
                        setSelectedIndex(i);
                        onNavigate?.(c.file, c.start);
                    }}
                >
                    <span
                        style={{
                            fontSize: 11,
                            color: priorityColor(c.priority),
                            fontWeight: 600,
                            backgroundColor: `${priorityColor(c.priority)}18`,
                            padding: "1px 6px",
                            borderRadius: 3,
                            flexShrink: 0,
                            marginTop: 1,
                        }}
                    >
                        {c.priority !== undefined ? `P${c.priority}` : "!"}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--aurora-text)" }}>
                            {c.title}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--aurora-text-secondary)", marginTop: 2 }}>
                            {c.file}:{c.start}{c.end ? `-${c.end}` : ""}
                        </div>
                        {selectedIndex === i && (
                            <div style={{ fontSize: 11, color: "var(--aurora-text)", marginTop: 4, lineHeight: 1.5 }}>
                                {c.body}
                            </div>
                        )}
                    </div>
                    {onDismiss && (
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                onDismiss(i);
                            }}
                            style={{
                                padding: "0 4px", fontSize: 14, lineHeight: 1,
                                backgroundColor: "transparent", color: "var(--aurora-text-secondary)",
                                border: "none", cursor: "pointer", borderRadius: 3, opacity: 0.6,
                            }}
                        >
                            x
                        </button>
                    )}
                </div>
            ))}
        </div>
    );
};