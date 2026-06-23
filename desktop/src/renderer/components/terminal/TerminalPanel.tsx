import React, { useRef, useEffect, useState, useCallback } from "react";
import { useStore } from "../../store";
import { useTerminal } from "../../hooks";
import { Terminal } from "xterm";
import { FitAddon } from "xterm-addon-fit";
import { WebLinksAddon } from "xterm-addon-web-links";
import "xterm/css/xterm.css";
import "../../styles.css";

export function TerminalPanel() {
    const colors = useStore((s) => s.themeColors);
    const workspace = useStore((s) => s.workspace);
    const activeSessionId = useStore((s) => s.activeSessionId);
    const terminalOpen = useStore((s) => s.terminalOpen);
    const setTerminalOpen = useStore((s) => s.setTerminalOpen);
    const terminalRef = useRef<HTMLDivElement>(null);
    const terminalId = activeSessionId ?? "default";
    const xtermRef = useRef<Terminal | null>(null);
    const fitAddonRef = useRef<FitAddon | null>(null);
    const [height, setHeight] = useState(() => Math.floor(window.innerHeight * 0.35));
    const [dragging, setDragging] = useState(false);
    const dragStartRef = useRef({ y: 0, height: 0 });
    const { write, resize: resizePty } = useTerminal(terminalId, workspace);

    // Initialize xterm.js
    useEffect(() => {
        if (!terminalRef.current || !terminalOpen) return;

        const term = new Terminal({
            cursorBlink: true,
            cursorStyle: "bar",
            fontSize: 13,
            fontFamily: "'Cascadia Code', 'Fira Code', 'Consolas', monospace",
            theme: {
                background: "#0d1117",
                foreground: "#e0e0e0",
                cursor: "#58a6ff",
                selectionBackground: "#264f78",
                black: "#1a1a2e",
                red: "#f85149",
                green: "#3fb950",
                yellow: "#d29922",
                blue: "#58a6ff",
                magenta: "#bc8cff",
                cyan: "#39c5cf",
                white: "#e6edf3",
                brightBlack: "#484f58",
                brightRed: "#ff7b72",
                brightGreen: "#56d364",
                brightYellow: "#e3b341",
                brightBlue: "#79c0ff",
                brightMagenta: "#d2a8ff",
                brightCyan: "#56d4dd",
                brightWhite: "#ffffff",
            },
            allowProposedApi: true,
            scrollback: 5000,
        });

        const fitAddon = new FitAddon();
        const webLinksAddon = new WebLinksAddon();

        term.loadAddon(fitAddon);
        term.loadAddon(webLinksAddon);
        term.open(terminalRef.current);
        fitAddon.fit();

        xtermRef.current = term;
        fitAddonRef.current = fitAddon;

        term.onData((data) => {
            write(data);
        });

        const cleanup = window.aurora?.terminal.onData(({ sessionId, data }) => {
            if (sessionId === terminalId && xtermRef.current) {
                xtermRef.current.write(data);
            }
        });

        setTimeout(() => {
            fitAddon.fit();
            if (xtermRef.current) {
                resizePty(xtermRef.current.cols, xtermRef.current.rows);
            }
        }, 100);

        return () => {
            term.dispose();
            xtermRef.current = null;
        };
    }, [terminalId, workspace, terminalOpen]);

    // Re-fit when terminal opens or height changes
    useEffect(() => {
        if (terminalOpen) {
            setTimeout(() => fitAddonRef.current?.fit(), 100);
        }
    }, [terminalOpen, height, terminalId]);

    // Drag resize handlers
    const handleDragStart = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        setDragging(true);
        dragStartRef.current = { y: e.clientY, height };
    }, [height]);

    useEffect(() => {
        if (!dragging) return;
        const handleMove = (e: MouseEvent) => {
            const delta = dragStartRef.current.y - e.clientY;
            const newHeight = Math.max(150, Math.min(window.innerHeight * 0.6, dragStartRef.current.height + delta));
            setHeight(newHeight);
        };
        const handleUp = () => setDragging(false);
        window.addEventListener("mousemove", handleMove);
        window.addEventListener("mouseup", handleUp);
        return () => {
            window.removeEventListener("mousemove", handleMove);
            window.removeEventListener("mouseup", handleUp);
        };
    }, [dragging]);

    const handleClose = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        setTerminalOpen(false);
    }, [setTerminalOpen]);

    const handleToggle = useCallback(() => {
        setTerminalOpen(!terminalOpen);
    }, [terminalOpen, setTerminalOpen]);

    return (
        <div
            className={`aurora-terminal-overlay ${terminalOpen ? "expanded" : "collapsed"}`}
            style={{ height: terminalOpen ? height : 28 }}
        >
            <div className="aurora-terminal-bg">
                {/* Resize handle */}
                {terminalOpen && (
                    <div
                        className="aurora-resize-handle"
                        onMouseDown={handleDragStart}
                        style={{ cursor: dragging ? "ns-resize" : "ns-resize" }}
                    />
                )}

                {/* Handle bar */}
                <div className="aurora-terminal-handle" onClick={handleToggle}>
                    <span className="label">
                        <span style={{ fontSize: 12 }}>
                            {terminalOpen ? "▼" : "▶"}
                        </span>
                        <span>终端控制台</span>
                        {terminalOpen && workspace && (
                            <span style={{ fontSize: 10, opacity: 0.5, marginLeft: 6 }}>
                                {workspace.split(/[/\\]/).pop()}
                            </span>
                        )}
                    </span>
                    <span className="actions">
                        {terminalOpen && (
                            <button className="aurora-terminal-close" onClick={handleClose} title="关闭">
                                ✕
                            </button>
                        )}
                    </span>
                </div>

                {/* Terminal body */}
                {terminalOpen && (
                    <div ref={terminalRef} className="aurora-terminal-body" />
                )}
            </div>
        </div>
    );
}
