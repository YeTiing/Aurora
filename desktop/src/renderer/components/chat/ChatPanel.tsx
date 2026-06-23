import React, { useState, useRef, useEffect, useLayoutEffect } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";
import "../../styles.css";

/* ═══════════════════════════════════════════════════════════════════════════
   ChatPanel — centered input · chat bubbles · warm companion UI
   ═══════════════════════════════════════════════════════════════════════════ */

export interface FileAttachment {
  name: string;
  path: string;
  dataUrl?: string;
  mimeType: string;
}

interface ChatPanelProps {
  onSend: (msg: string, files?: FileAttachment[]) => void;
  onCancel: () => void;
}

const WELCOME_ACTIONS = [
  t("quickAnalyzeCodebase"),
  t("quickCreateProject"),
  t("quickExplainCode"),
  t("quickWriteTests"),
];

export function ChatPanel({ onSend, onCancel }: ChatPanelProps) {
  const colors = useStore((s) => s.themeColors);
  const sessions = useStore((s) => s.sessions);
  const activeSessionId = useStore((s) => s.activeSessionId);
  const isStreaming = useStore((s) => s.isStreaming);
  const llmModel = useStore((s) => s.llmModel);
  const llmProvider = useStore((s) => s.llmProvider);
  const setLLMModel = useStore((s) => s.setLLMModel);
  const sandboxMode = useStore((s) => s.sandboxMode);
  const setSandboxMode = useStore((s) => s.setSandboxMode);

  const [input, setInput] = useState("");
  const [files, setFiles] = useState<FileAttachment[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [sandboxMenuOpen, setSandboxMenuOpen] = useState(false);

  const [availableModels, setAvailableModels] = useState<{id:string;label:string;provider:string}[]>([]);

  // 从后端拉取可用模型列表
  const fetchModels = async () => {
    try {
      const res = await fetch("http://127.0.0.1:9876/models");
      const data = await res.json();
      if (data.models?.length) {
        setAvailableModels(data.models.map((m: any) => ({
          id: m.id,
          label: m.id,
          provider: m.provider || "custom",
        })));
      }
    } catch {
      // Fallback if backend not reachable
      setAvailableModels([
        { id: "gpt-4o", label: "gpt-4o", provider: "openai" },
        { id: "gpt-4o-mini", label: "gpt-4o-mini", provider: "openai" },
        { id: "claude-3-5-sonnet", label: "claude-3-5-sonnet", provider: "claude" },
        { id: "deepseek-chat", label: "deepseek-chat", provider: "deepseek" },
      ]);
    }
  };

  // 初次加载拉模型列表
  useEffect(() => { fetchModels(); }, [llmProvider]);

  const SANDBOX_OPTIONS = [
    { id: "full-access" as const, label: "完全访问", icon: "🌐" },
    { id: "workspace-only" as const, label: "仅工作区", icon: "📁" },
    { id: "read-only" as const, label: "只读", icon: "🔒" },
  ];

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const activeSession = sessions.find((s: any) => s.id === activeSessionId);
  const messages: any[] = activeSession?.messages ?? [];
  const plan: any[] = activeSession?.plan ?? [];
  const toolLogs: any[] = activeSession?.toolLogs ?? [];

  /* ── Auto-scroll & focus ────────────────────────────────────────────── */
  useLayoutEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [activeSessionId]);

  /* Auto-grow + vertical centering */
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.paddingTop = "0px";
    el.style.paddingBottom = "0px";
    const sh = el.scrollHeight;
    const lineH = 23; // 15px * 1.55 line-height
    const minH = 48;
    const maxH = 200;
    const targetH = Math.min(Math.max(sh, minH), maxH);
    el.style.height = targetH + "px";
    // 文字少时用顶部分配让文字竖直居中
    if (targetH > sh + 4) {
      const extra = (targetH - sh) / 2;
      el.style.paddingTop = Math.floor(extra) + "px";
      el.style.paddingBottom = Math.ceil(extra) + "px";
    }
  }, [input]);

  /* ── Send ───────────────────────────────────────────────────────────── */
  const handleSend = () => {
    const trimmed = input.trim();
    if (!trimmed && files.length === 0) return;
    onSend(trimmed, files.length > 0 ? files : undefined);
    setInput("");
    setFiles([]);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  /* ── File attachment ────────────────────────────────────────────────── */
  const readDroppedFiles = async (fileList: FileList) => {
    const newFiles: FileAttachment[] = [];
    for (let i = 0; i < fileList.length; i++) {
      const f = fileList[i];
      const entry: FileAttachment = {
        name: f.name,
        path: (f as any).path || f.name,
        mimeType: f.type || "application/octet-stream",
      };
      if (f.type.startsWith("image/")) {
        entry.dataUrl = await readAsDataURL(f);
      }
      newFiles.push(entry);
    }
    setFiles((prev) => [...prev, ...newFiles]);
  };

  const readAsDataURL = (file: File): Promise<string> =>
    new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.readAsDataURL(file);
    });

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      await readDroppedFiles(e.dataTransfer.files);
    }
  };

  const handleFilePick = () => fileInputRef.current?.click();

  const handleFileInputChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      await readDroppedFiles(e.target.files);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const isImage = (mime: string) => mime.startsWith("image/");
  const canSend = !!(input.trim() || files.length > 0) && !isStreaming;

  /* ── Render helpers ─────────────────────────────────────────────────── */

  const renderPlanSteps = () => {
    if (plan.length === 0) return null;
    return (
      <div className="plan-steps">
        {plan.map((step: any, i: number) => (
          <div key={i} className={`plan-step ${step.status}`}>
            <span className={`plan-step-indicator ${step.status}`} />
            <span>{step.description}</span>
          </div>
        ))}
      </div>
    );
  };

  const renderToolLogs = () => {
    if (toolLogs.length === 0) return null;
    return (
      <>
        {toolLogs.map((log: any, i: number) => (
          <ToolResultCard key={i} log={log} />
        ))}
      </>
    );
  };

  const renderMessage = (msg: any) => {
    if (msg.role === "system") {
      return (
        <div key={msg.id} className="chat-bubble system fade-in">
          {msg.content}
        </div>
      );
    }

    const isUser = msg.role === "user";
    return (
      <div
        key={msg.id}
        className={`chat-bubble ${isUser ? "user" : "agent"} fade-in`}
      >
        {!isUser && <div className="sender-label">{t("welcomeTitle")}</div>}
        <div className="markdown-content">{renderContent(msg.content)}</div>
      </div>
    );
  };

  /* Simple markdown-like rendering for code blocks / inline code */
  const renderContent = (text: string): React.ReactNode => {
    if (!text) return null;

    /* Split on code fences ``` */
    const parts = text.split(/(```[\s\S]*?```)/g);

    return parts.map((part, i) => {
      const match = part.match(/^```(\w*)\n?([\s\S]*?)```$/);
      if (match) {
        const lang = match[1] || "";
        const code = match[2].replace(/\n$/, "");
        return (
          <pre key={i}>
            {lang && <div style={{ fontSize: 10, color: "var(--aurora-text-muted)", marginBottom: 4 }}>{lang}</div>}
            <code>{code}</code>
          </pre>
        );
      }

      /* Inline code rendering */
      const inlineParts = part.split(/(`[^`]+`)/g);
      return (
        <span key={i}>
          {inlineParts.map((inline, j) => {
            if (inline.startsWith("`") && inline.endsWith("`")) {
              return <code key={j}>{inline.slice(1, -1)}</code>;
            }
            /* Bold */
            const boldParts = inline.split(/(\*\*[^*]+\*\*)/g);
            return (
              <React.Fragment key={j}>
                {boldParts.map((bp, k) => {
                  if (bp.startsWith("**") && bp.endsWith("**")) {
                    return <strong key={k}>{bp.slice(2, -2)}</strong>;
                  }
                  return <span key={k}>{bp}</span>;
                })}
              </React.Fragment>
            );
          })}
        </span>
      );
    });
  };

  /* ── Main render ────────────────────────────────────────────────────── */
  return (
    <div className="chat-panel">
      {/* Header */}
      <div className="chat-header">
        <span className="chat-header-title">
          {activeSession?.title || t("noMessages")}
        </span>
        {isStreaming && (
          <div className="stream-indicator">
            <span className="stream-dot" />
            <span className="stream-dot" />
            <span className="stream-dot" />
          </div>
        )}
      </div>

      {/* Messages area */}
      <div className="chat-messages scrollbar-thin">
        {messages.length === 0 && plan.length === 0 ? (
          /* ── Welcome / empty state ─────────────────────────────────── */
          <div className="welcome slide-up">
            <div className="welcome-icon">✦</div>
            <div className="welcome-title">{t("welcomeTitle")}</div>
            <div className="welcome-subtitle">
              {t("askAurora")}
            </div>
            <div className="welcome-actions">
              {WELCOME_ACTIONS.map((action) => (
                <button
                  key={action}
                  className="welcome-chip"
                  onClick={() => {
                    setInput(action);
                    inputRef.current?.focus();
                  }}
                >
                  {action}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {/* Plan steps */}
            {renderPlanSteps()}

            {/* Tool logs */}
            {renderToolLogs()}

            {/* Messages */}
            {messages.map(renderMessage)}

            {/* Streaming placeholder */}
            {isStreaming && !messages.some((m: any) => m.role === "assistant" && m.content === "") && (
              <div className="chat-bubble agent fade-in">
                <div className="sender-label">{t("welcomeTitle")}</div>
                <div className="stream-indicator" style={{ padding: "4px 0" }}>
                  <span className="stream-dot" />
                  <span className="stream-dot" />
                  <span className="stream-dot" />
                </div>
              </div>
            )}

            {/* Cancel while streaming */}
            {isStreaming && (
              <div className="cancel-bar">
                <button className="btn" onClick={onCancel}>
                  {t("cancel")}
                </button>
              </div>
            )}
          </>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ── Input area ────────────────────────────────────────────────── */}
      <div className="input-bar-container">
        <div
          className="input-bar"
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {/* File chips */}
          {files.length > 0 && (
            <div className="file-chips">
              {files.map((f, i) => (
                <div key={i} className="file-chip">
                  {f.dataUrl && isImage(f.mimeType) ? (
                    <img src={f.dataUrl} alt={f.name} />
                  ) : null}
                  <span
                    style={{
                      maxWidth: 140,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {f.name}
                  </span>
                  <button
                    className="file-chip-remove"
                    onClick={() => removeFile(i)}
                    title={t("remove")}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Controls row: model + sandbox */}
          <div className="input-controls-row">
            {/* Model selector */}
            <div className="model-selector-wrapper">
              <button
                className="model-selector-btn"
                onClick={() => setModelMenuOpen(!modelMenuOpen)}
                title="切换模型"
              >
                <span className="model-selector-dot" />
                <span className="model-selector-label">{llmModel}</span>
                <span className="model-selector-arrow">▾</span>
              </button>
              {modelMenuOpen && (
                <>
                  <div className="model-menu-backdrop" onClick={() => setModelMenuOpen(false)} />
                  <div className="model-menu">
                    {(availableModels.length > 0 ? availableModels : [{id:llmModel, label:llmModel, provider:"current"}]).map(m => (
                      <button
                        key={m.id}
                        className={`model-menu-item${m.id === llmModel ? " active" : ""}`}
                        onClick={() => {
                          setLLMModel(m.id);
                          setModelMenuOpen(false);
                          // 同时通知后端更新模型
                          fetch("http://127.0.0.1:9876/settings", {
                            method: "POST",
                            headers: {"Content-Type":"application/json"},
                            body: JSON.stringify({model: m.id}),
                          }).catch(() => {});
                        }}
                      >
                        <span className="model-menu-name">{m.label}</span>
                        <span className="model-menu-provider">{m.provider}</span>
                        {m.id === llmModel && <span className="model-menu-check">✓</span>}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Sandbox mode dropdown */}
            <div className="sandbox-selector-wrapper">
              <button
                className="sandbox-selector-btn"
                onClick={() => setSandboxMenuOpen(!sandboxMenuOpen)}
                title="沙盒权限"
              >
                <span className="sandbox-selector-icon">🔐</span>
                <span className="sandbox-selector-label">{SANDBOX_OPTIONS.find(o => o.id === sandboxMode)?.label || "完全访问"}</span>
                <span className="sandbox-selector-arrow">▾</span>
              </button>
              {sandboxMenuOpen && (
                <>
                  <div className="sandbox-menu-backdrop" onClick={() => setSandboxMenuOpen(false)} />
                  <div className="sandbox-menu">
                    {SANDBOX_OPTIONS.map(opt => (
                      <button
                        key={opt.id}
                        className={`sandbox-menu-item${sandboxMode === opt.id ? " active" : ""}`}
                        onClick={() => { setSandboxMode(opt.id); setSandboxMenuOpen(false); }}
                      >
                        <span className="sandbox-menu-icon">{opt.icon}</span>
                        <span className="sandbox-menu-name">{opt.label}</span>
                        {sandboxMode === opt.id && <span className="sandbox-menu-check">✓</span>}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Input row */}
          <div className={`input-row${dragOver ? " drag-over" : ""}`}>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              onChange={handleFileInputChange}
              style={{ display: "none" }}
            />

            <textarea
              ref={(el) => {
                (inputRef as any).current = el;
                (textareaRef as any).current = el;
              }}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t("typeMessage") + " (Enter 发送, Shift+Enter 换行)"}
            />

            <button
              className="input-attach-btn"
              onClick={handleFilePick}
              title={t("attachFiles")}
            >
              📎
            </button>

            <button
              className={`btn btn-accent btn-send${!canSend ? "" : ""}`}
              onClick={handleSend}
              disabled={!canSend}
              style={{
                opacity: canSend ? 1 : 0.3,
                cursor: canSend ? "pointer" : "not-allowed",
              }}
            >
              →
            </button>
          </div>

          {/* Drop overlay */}
          {dragOver && (
            <div className="drop-overlay">
              <span className="drop-overlay-text">{t("dropFilesToAttach")}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Tool Result Card (collapsible) ───────────────────────────────────── */
function ToolResultCard({ log }: { log: any }) {
  const [open, setOpen] = useState(false);
  const isEnd = log.type === "tool_end" || log.type === "tool_output";
  const success = log.success !== false;

  return (
    <div className="tool-result">
      <div className="tool-result-header" onClick={() => setOpen(!open)}>
        <span className="tool-icon">{open ? "▾" : "▸"}</span>
        <span className="tool-name">{log.tool}</span>
        {isEnd && (
          <span className={`tool-status ${success ? "success" : "error"}`}>
            {success ? "OK" : "ERR"}
          </span>
        )}
      </div>
      {open && (
        <div className="tool-result-body">
          {log.output || log.error || JSON.stringify(log.args, null, 2) || "(no output)"}
        </div>
      )}
    </div>
  );
}
