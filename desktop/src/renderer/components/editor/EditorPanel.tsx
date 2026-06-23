import React, { useRef, useEffect, useState, useCallback } from "react";
import Editor, { DiffEditor } from "@monaco-editor/react";
import { useStore } from "../../store";
import type { editor } from "monaco-editor";

export function EditorPanel() {
    const editorFontSize = useStore((s: any) => s.editorFontSize ?? 14);
    const editorTabSize = useStore((s: any) => s.editorTabSize ?? 4);
    const editorWordWrap = useStore((s: any) => s.editorWordWrap ?? false);
    const editorMinimap = useStore((s: any) => s.editorMinimap ?? false);
    const colors = useStore((s) => s.themeColors);
    const openFiles = useStore((s) => s.openFiles);
    const activeFile = useStore((s) => s.activeFile);
    const setActiveFile = useStore((s) => s.setActiveFile);
    const closeFile = useStore((s) => s.closeFile);
    const theme = useStore((s) => s.theme);
    const [fileContent, setFileContent] = useState<string>("");
    const [originalContent, setOriginalContent] = useState<string>("");
    const [showDiff, setShowDiff] = useState(false);
    const [language, setLanguage] = useState("plaintext");
    const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
    const [dirty, setDirty] = useState(false);

    const detectLanguage = (filePath: string): string => {
        const ext = filePath.split(".").pop()?.toLowerCase();
        const langMap: Record<string, string> = {
            py: "python", ts: "typescript", tsx: "typescriptreact",
            js: "javascript", jsx: "javascriptreact", json: "json",
            html: "html", css: "css", md: "markdown", yaml: "yaml", yml: "yaml",
            go: "go", rs: "rust", java: "java", c: "c", cpp: "cpp",
            h: "c", hpp: "cpp", sql: "sql", sh: "shell", ps1: "powershell",
            toml: "toml", xml: "xml", svg: "xml", dockerfile: "dockerfile",
        };
        return langMap[ext || ""] || "plaintext";
    };

    useEffect(() => {
        if (activeFile) {
            setLanguage(detectLanguage(activeFile));
            window.aurora?.file.read(activeFile).then((content) => {
                if (typeof content === "string") {
                    setOriginalContent(content);
                    setFileContent(content);
                    setShowDiff(false);
                }
            });
        } else {
            setFileContent("");
            setOriginalContent("");
            setShowDiff(false);
        }
    }, [activeFile]);

    const handleSave = useCallback(async () => {
        if (activeFile && fileContent) {
            await window.aurora?.file.write(activeFile, fileContent);
            setOriginalContent(fileContent);
            setShowDiff(false);
            setDirty(false);
        }
    }, [activeFile, fileContent]);

    const handleEditorMount = useCallback((editor: editor.IStandaloneCodeEditor) => {
        editorRef.current = editor;
        editor.addAction({
            id: "save-file",
            label: "Save File",
            keybindings: [2048 | 49], // Ctrl+S
            run: handleSave,
        });
    }, [handleSave]);

    const toggleDiff = () => {
        if (fileContent !== originalContent) {
            setShowDiff(!showDiff);
        }
    };

    const hasChanges = fileContent !== originalContent;

    return (
        <div style={{ height: "100%", display: "flex", flexDirection: "column", backgroundColor: colors.bg }}>
            {/* Tab bar */}
            {openFiles.length > 0 && (
                <div style={{
                    display: "flex", borderBottom: `1px solid ${colors.border}`,
                    overflowX: "auto", backgroundColor: colors.bgSecondary, flexShrink: 0,
                }}>
                    {openFiles.map((f) => {
                        const fname = f.split("/").pop()?.split("\\").pop() || f;
                        const isActive = f === activeFile;
                        return (
                            <div key={f} onClick={() => setActiveFile(f)} style={{
                                padding: "6px 12px", fontSize: 12, cursor: "pointer",
                                display: "flex", alignItems: "center", gap: 6,
                                borderRight: `1px solid ${colors.border}`,
                                backgroundColor: isActive ? colors.bg : "transparent",
                                color: isActive ? colors.text : colors.textSecondary,
                                whiteSpace: "nowrap",
                            }}>
                                <span>{fname}{hasChanges && isActive ? " ●" : ""}</span>
                                <span onClick={(e) => { e.stopPropagation(); closeFile(f); }}
                                    style={{ fontSize: 10, opacity: 0.5, cursor: "pointer" }}>✕</span>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* Diff toggle bar */}
            {activeFile && (
                <div style={{
                    display: "flex", alignItems: "center", gap: 8, padding: "4px 12px",
                    borderBottom: hasChanges ? `1px solid ${colors.warning}` : `1px solid ${colors.border}`,
                    backgroundColor: colors.bgSecondary, fontSize: 11, flexShrink: 0,
                }}>
                    <span style={{ color: colors.textSecondary }}>{activeFile}</span>
                    <span style={{ color: colors.textSecondary }}>·</span>
                    <span style={{ color: colors.textSecondary }}>{language}</span>
                    {hasChanges && (
                        <>
                            <span style={{ flex: 1 }} />
                            <button onClick={toggleDiff} style={{
                                padding: "2px 10px", borderRadius: 4, border: `1px solid ${colors.border}`,
                                cursor: "pointer", fontSize: 11, backgroundColor: colors.bg,
                                color: showDiff ? colors.accent : colors.textSecondary,
                            }}>
                                {showDiff ? "Hide Diff" : "Show Diff"}
                            </button>
                            <button onClick={handleSave} style={{
                                padding: "2px 10px", borderRadius: 4, border: "none",
                                cursor: "pointer", fontSize: 11, backgroundColor: colors.accent,
                                color: "#fff", fontWeight: 600,
                            }}>Save</button>
                        </>
                    )}
                </div>
            )}

            {/* Editor area */}
            <div style={{ flex: 1, overflow: "hidden" }}>
                {activeFile ? (
                    showDiff && originalContent !== fileContent ? (
                        <DiffEditor
                            height="100%"
                            language={language}
                            original={originalContent}
                            modified={fileContent}
                            theme={theme === "dark" ? "vs-dark" : "vs"}
                            options={{
                                readOnly: true,
                                renderSideBySide: true,
                                minimap: { enabled: editorMinimap },
                                fontSize: editorFontSize,
                                fontFamily: "'Cascadia Code', 'Fira Code', Consolas, monospace",
                                scrollBeyondLastLine: false,
                                lineNumbers: "on",
                                renderWhitespace: 'selection',
                                // @ts-ignore IDiffEditorConstructionOptions lacks tabSize
                                tabSize: editorTabSize,
                            }}
                        />
                    ) : (
                        <Editor
                            height="100%"
                            language={language}
                            value={fileContent}
                            onChange={(v) => setFileContent(v || "")}
                            onMount={handleEditorMount}
                            theme={theme === "dark" ? "vs-dark" : "vs"}
                            options={{
                                minimap: { enabled: editorMinimap },
                                fontSize: editorFontSize,
                                fontFamily: "'Cascadia Code', 'Fira Code', Consolas, monospace",
                                scrollBeyondLastLine: false,
                                lineNumbers: "on",
                                renderWhitespace: 'selection',
                                tabSize: editorTabSize,
                                bracketPairColorization: { enabled: true },
                                automaticLayout: true,
                                wordWrap: editorWordWrap ? "on" : "off",
                                cursorBlinking: "smooth",
                                smoothScrolling: true,
                                suggest: { showKeywords: true, showSnippets: true },
                            }}
                        />
                    )
                ) : (
                    <div style={{
                        height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
                        color: colors.textSecondary, fontSize: editorFontSize, flexDirection: "column", gap: 8
                    }}>
                        <span style={{ fontSize: 48, opacity: 0.3 }}>✦</span>
                        <span>Open a file or start a chat to begin</span>
                        <span style={{ fontSize: 11, opacity: 0.5 }}>Ctrl+O to open · Ctrl+S to save</span>
                    </div>
                )}
            </div>
        </div>
    );
}
