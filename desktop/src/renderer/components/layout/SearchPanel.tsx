import React, { useRef, useState, useEffect } from "react";
import { useStore } from "../../store";

export function SearchPanel() {
    const colors = useStore((s) => s.themeColors);
    const searchQuery = useStore((s) => s.searchQuery);
    const setSearchQuery = useStore((s) => s.setSearchQuery);
    const showSearch = useStore((s) => s.showSearch);
    const toggleSearch = useStore((s) => s.toggleSearch);
    const workspace = useStore((s) => s.workspace);
    const openFile = useStore((s) => s.openFile);
    const [results, setResults] = useState<string[]>([]);
    const [searching, setSearching] = useState(false);
    const [options, setOptions] = useState({ caseSensitive: false, wholeWord: false, regex: false });
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (showSearch) inputRef.current?.focus();
    }, [showSearch]);

    useEffect(() => {
        if (!searchQuery.trim()) { setResults([]); return; }
        const timer = setTimeout(() => doSearch(), 300);
        return () => clearTimeout(timer);
    }, [searchQuery, options]);

    async function doSearch() {
        setSearching(true);
        try {
            const result = await window.aurora?.searchInFiles({
                query: searchQuery,
                workspace,
                caseSensitive: options.caseSensitive,
                wholeWord: options.wholeWord,
                regex: options.regex,
            });
            if (result && Array.isArray(result)) {
                setResults(result.slice(0, 100));
            }
        } catch { setResults([]); }
        setSearching(false);
    }

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Escape") toggleSearch();
        if (e.key === "Enter" && results.length > 0) {
            const [filePath] = results[0].split(":");
            if (filePath) openFile(workspace + "/" + filePath);
        }
    };

    if (!showSearch) return null;

    return (
        <div style={{
            position: "fixed", top: 48, left: "50%", transform: "translateX(-50%)",
            width: 560, maxHeight: "60vh", backgroundColor: colors.surface,
            border: `1px solid ${colors.border}`, borderRadius: 8,
            boxShadow: "0 8px 32px rgba(0,0,0,0.4)", zIndex: 90,
            display: "flex", flexDirection: "column", animation: "slideUp 0.15s ease-out",
        }}>
            {/* Search input */}
            <div style={{ display: "flex", alignItems: "center", padding: "8px 12px", gap: 8, borderBottom: `1px solid ${colors.border}` }}>
                <span style={{ color: colors.textSecondary }}>🔍</span>
                <input ref={inputRef} value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={handleKeyDown} placeholder="Search in files..."
                    style={{
                        flex: 1, border: "none", background: "transparent", color: colors.text,
                        fontSize: 13, outline: "none", fontFamily: "inherit",
                    }} />
                {/* Options */}
                <ToggleOpt label="Aa" active={options.caseSensitive} onClick={() => setOptions({...options, caseSensitive: !options.caseSensitive})} colors={colors} />
                <ToggleOpt label="W" active={options.wholeWord} onClick={() => setOptions({...options, wholeWord: !options.wholeWord})} colors={colors} />
                <ToggleOpt label=".*" active={options.regex} onClick={() => setOptions({...options, regex: !options.regex})} colors={colors} />
                <span style={{ fontSize: 10, color: colors.textSecondary }}>{results.length} results</span>
            </div>

            {/* Results */}
            <div style={{ flex: 1, overflow: "auto", maxHeight: "50vh" }}>
                {searching ? (
                    <div style={{ padding: 16, color: colors.textSecondary, fontSize: 12 }}>Searching...</div>
                ) : results.length === 0 && searchQuery ? (
                    <div style={{ padding: 16, color: colors.textSecondary, fontSize: 12 }}>No results</div>
                ) : (
                    results.map((r, i) => {
                        const [filePath, line, ...rest] = r.split(":");
                        const content = rest.join(":").trim();
                        return (
                            <div key={i} onClick={() => {
                                openFile(workspace + "/" + filePath);
                                toggleSearch();
                            }} style={{
                                padding: "4px 12px", cursor: "pointer", fontSize: 12,
                                borderBottom: `1px solid ${colors.border}`,
                                display: "flex", gap: 8, alignItems: "baseline",
                            }}
                                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = colors.border)}
                                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
                            >
                                <span style={{ color: colors.accent, whiteSpace: "nowrap", fontSize: 11 }}>{filePath}:{line}</span>
                                <span style={{ color: colors.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {content}
                                </span>
                            </div>
                        );
                    })
                )}
            </div>
        </div>
    );
}

function ToggleOpt({ label, active, onClick, colors }: { label: string; active: boolean; onClick: () => void; colors: any }) {
    return (
        <button onClick={onClick} style={{
            padding: "2px 6px", borderRadius: 3, border: `1px solid ${active ? colors.accent : colors.border}`,
            backgroundColor: active ? `${colors.accent}20` : "transparent", color: active ? colors.accent : colors.textSecondary,
            cursor: "pointer", fontSize: 11, fontWeight: active ? 600 : 400,
        }}>{label}</button>
    );
}