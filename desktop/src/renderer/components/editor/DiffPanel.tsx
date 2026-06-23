import React, { useState, useMemo, useCallback } from "react";
import { useStore } from "../../store";
import { useTheme } from "../../hooks";

interface DiffBlock {
    header: string;
    oldStart: number;
    oldCount: number;
    newStart: number;
    newCount: number;
    lines: DiffLine[];
}

interface DiffLine {
    type: "context" | "added" | "removed";
    content: string;
    oldLineNum?: number;
    newLineNum?: number;
}

function parseUnifiedDiff(diffText: string): DiffBlock[] {
    const blocks: DiffBlock[] = [];
    let current: DiffBlock | null = null;

    for (const line of diffText.split("\n")) {
        if (line.startsWith("@@")) {
            const match = line.match(/@@ -(\d+),?(\d*)? \+(\d+),?(\d*)? @@/);
            if (match) {
                if (current) blocks.push(current);
                current = {
                    header: line,
                    oldStart: parseInt(match[1]),
                    oldCount: parseInt(match[2] || "1"),
                    newStart: parseInt(match[3]),
                    newCount: parseInt(match[4] || "1"),
                    lines: [],
                };
            }
        } else if (current) {
            if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("diff ")) {
                continue;
            }
            if (line.startsWith("+")) {
                current.lines.push({ type: "added", content: line.substring(1), newLineNum: current.newStart++ });
            } else if (line.startsWith("-")) {
                current.lines.push({ type: "removed", content: line.substring(1), oldLineNum: current.oldStart++ });
            } else {
                current.lines.push({
                    type: "context",
                    content: line.startsWith(" ") ? line.substring(1) : line,
                    oldLineNum: current.oldStart++,
                    newLineNum: current.newStart++,
                });
            }
        }
    }
    if (current) blocks.push(current);
    return blocks;
}

interface DiffPanelProps {
    diffText: string;
    onAccept?: (text: string) => void;
    onReject?: () => void;
}

export const DiffPanel: React.FC<DiffPanelProps> = ({ diffText, onAccept, onReject }) => {
    const colors = useTheme();
    const [accepted, setAccepted] = useState(false);
    const filePath = useMemo(() => {
        const match = diffText.match(/^\+\+\+ b\/(.+)$/m);
        return match ? match[1] : null;
    }, [diffText]);

    const blocks = useMemo(() => parseUnifiedDiff(diffText), [diffText]);

    const addedCount = useMemo(
        () => blocks.reduce((sum, b) => sum + b.lines.filter((l) => l.type === "added").length, 0),
        [blocks]
    );
    const removedCount = useMemo(
        () => blocks.reduce((sum, b) => sum + b.lines.filter((l) => l.type === "removed").length, 0),
        [blocks]
    );

    const handleAccept = useCallback(() => {
        setAccepted(true);
        onAccept?.(diffText);
    }, [diffText, onAccept]);

    const handleReject = useCallback(() => {
        onReject?.();
    }, [onReject]);

    const lineStyle = (type: DiffLine["type"]): React.CSSProperties => {
        const base: React.CSSProperties = {
            fontFamily: "'Cascadia Code', 'Fira Code', 'Consolas', monospace",
            fontSize: 12,
            lineHeight: "20px",
            padding: "0 8px",
            whiteSpace: "pre",
            display: "flex",
        };
        switch (type) {
            case "added":
                return { ...base, backgroundColor: "#1a3a20", color: "#a3d9a5", borderLeft: "3px solid #3fb950" };
            case "removed":
                return { ...base, backgroundColor: "#3a1a1a", color: "#f9b0b0", borderLeft: "3px solid #f85149" };
            default:
                return { ...base, backgroundColor: "transparent", color: colors.textSecondary };
        }
    };

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", backgroundColor: colors.bg }}>
            <div style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "8px 12px", borderBottom: `1px solid ${colors.border}`,
                backgroundColor: colors.bgSecondary,
            }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: colors.text }}>
                    {filePath ? `Changes: ${filePath}` : "Proposed Changes"}
                </div>
                <div style={{ display: "flex", gap: 8, fontWeight: 500 }}>
                    <span style={{ color: colors.success, fontSize: 13 }}>+{addedCount}</span>
                    <span style={{ color: colors.error, fontSize: 13 }}>-{removedCount}</span>
                </div>
                {!accepted && (
                    <div style={{ display: "flex", gap: 8 }}>
                        <button
                            onClick={handleAccept}
                            style={{
                                padding: "4px 16px", fontSize: 12, fontWeight: 600,
                                backgroundColor: colors.success, color: "#fff",
                                border: "none", borderRadius: 6, cursor: "pointer",
                            }}
                        >
                            Accept All
                        </button>
                        <button
                            onClick={handleReject}
                            style={{
                                padding: "4px 16px", fontSize: 12, fontWeight: 600,
                                backgroundColor: colors.error, color: "#fff",
                                border: "none", borderRadius: 6, cursor: "pointer",
                            }}
                        >
                            Reject
                        </button>
                    </div>
                )}
            </div>

            <div style={{ flex: 1, overflow: "auto", padding: "4px 0" }}>
                {blocks.map((block, bi) => (
                    <div key={bi}>
                        <div style={{
                            fontSize: 12, color: colors.textSecondary, fontFamily: "monospace",
                            padding: "4px 12px", backgroundColor: "rgba(88,166,255,0.08)",
                            borderTop: bi > 0 ? `1px solid ${colors.border}` : "none",
                            borderBottom: `1px solid ${colors.border}`,
                        }}>
                            {block.header}
                        </div>
                        {block.lines.map((line, li) => (
                            <div key={li} style={lineStyle(line.type)}>
                                <span style={{ minWidth: 40, textAlign: "right", marginRight: 8, color: colors.textSecondary, userSelect: "none", opacity: 0.6 }}>
                                    {line.oldLineNum || ""}
                                </span>
                                <span style={{ minWidth: 40, textAlign: "right", marginRight: 12, color: colors.textSecondary, userSelect: "none", opacity: 0.6 }}>
                                    {line.newLineNum || ""}
                                </span>
                                <span>{line.content}</span>
                            </div>
                        ))}
                    </div>
                ))}
            </div>
        </div>
    );
};

export type { DiffBlock, DiffLine };