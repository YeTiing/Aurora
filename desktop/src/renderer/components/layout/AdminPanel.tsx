import React, { useEffect, useState } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";

const API = "http://127.0.0.1:9876";

interface PluginInfo { name: string; loaded: boolean; version?: string; }
interface MCPServer { name: string; state: string; transport?: string; }
interface ProcessInfo { proc_id: string; cmd: string; status: string; pid?: number; }
interface BrowserPage { id: string; title: string; url: string; }

type AdminTab = "plugins" | "mcp" | "processes" | "browser" | "approval";

export function AdminPanel({ onClose }: { onClose: () => void }) {
  const colors = useStore((s) => s.themeColors);
  const [tab, setTab] = useState<AdminTab>("plugins");
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [pages, setPages] = useState<BrowserPage[]>([]);
  const [approvalPending, setApprovalPending] = useState<any[]>([]);
  const [approvalStatus, setApprovalStatus] = useState<string>("");
  const [navUrl, setNavUrl] = useState("");
  const [browserMsg, setBrowserMsg] = useState("");

  const api = async (path: string, opts?: RequestInit) => {
    try { const r = await fetch(API + path, opts); return await r.json(); }
    catch { return null; }
  };

  const loadAll = async () => {
    const [p, m, pr, pa, ps] = await Promise.all([
      api("/plugins"), api("/mcp/servers"), api("/processes"),
      api("/approval/pending"), api("/approval/status"),
    ]);
    if (Array.isArray(p)) setPlugins(p);
    if (Array.isArray(m)) setMcpServers(m);
    if (Array.isArray(pr)) setProcesses(pr);
    if (Array.isArray(pa)) setApprovalPending(pa);
    if (ps?.mode) setApprovalStatus(ps.mode);
  };

  const loadPages = async () => {
    const r = await api("/browser/pages");
    if (r?.pages) setPages(r.pages);
  };

  useEffect(() => { loadAll(); const i = setInterval(loadAll, 5000); return () => clearInterval(i); }, []);
  useEffect(() => { if (tab === "browser") loadPages(); }, [tab]);

  const tabBtn = (t: AdminTab, label: string) => (
    <button onClick={() => setTab(t)} style={{
      padding: "6px 12px", border: "none", borderRadius: 4, cursor: "pointer",
      background: tab === t ? (colors.accent || "#8b5cf6") : "transparent",
      color: tab === t ? "#fff" : colors.textSecondary, fontSize: 12, fontWeight: 600,
    }}>{label}</button>
  );

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 2000, display: "flex",
      alignItems: "center", justifyContent: "center",
      backgroundColor: "rgba(0,0,0,0.5)", backdropFilter: "blur(2px)",
    }}>
      <div style={{
        width: 680, maxHeight: "85vh", backgroundColor: colors.bg, borderRadius: 12,
        border: `1px solid ${colors.border}`, display: "flex", flexDirection: "column",
        boxShadow: "0 20px 60px rgba(0,0,0,0.5)", overflow: "hidden",
      }}>
        {/* Header */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "12px 16px", borderBottom: `1px solid ${colors.border}`,
        }}>
          <span style={{ fontWeight: 700, fontSize: 15 }}>🔧 Admin Panel</span>
          <button onClick={onClose} style={{
            background: "none", border: "none", color: colors.textSecondary,
            fontSize: 18, cursor: "pointer",
          }}>✕</button>
        </div>
        {/* Tabs */}
        <div style={{ display: "flex", gap: 6, padding: "8px 16px", borderBottom: `1px solid ${colors.border}` }}>
          {tabBtn("plugins", t("pluginsTab"))}
          {tabBtn("mcp", t("mcpTab"))}
          {tabBtn("processes", t("processesTab"))}
          {tabBtn("browser", t("browserTab"))}
          {tabBtn("approval", t("approvalTab"))}
        </div>
        {/* Content */}
        <div style={{ flex: 1, overflow: "auto", padding: 16, fontSize: 12 }}>
          {/* Plugins */}
          {tab === "plugins" && (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 10 }}>Plugin Manager ({plugins.length})</div>
              {plugins.length === 0 && <div style={{ color: colors.textSecondary }}>No plugins registered.</div>}
              {plugins.map((p) => (
                <div key={p.name} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "8px 12px", marginBottom: 6, borderRadius: 6,
                  border: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary,
                }}>
                  <div>
                    <span style={{ fontWeight: 600 }}>{p.name}</span>
                    <span style={{ marginLeft: 8, fontSize: 10, color: p.loaded ? "#22c55e" : "#ef4444" }}>
                      {p.loaded ? "● loaded" : "○ unloaded"}
                    </span>
                    {p.version && <span style={{ marginLeft: 8, color: colors.textSecondary }}>v{p.version}</span>}
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={async () => { await api(`/plugins/${p.name}/load`, { method: "POST" }); loadAll(); }}
                      style={btnSm(colors)}>Load</button>
                    <button onClick={async () => { await api(`/plugins/${p.name}/unload`, { method: "POST" }); loadAll(); }}
                      style={btnSm(colors)}>Unload</button>
                    <button onClick={async () => { await api(`/plugins/${p.name}/reload`, { method: "POST" }); loadAll(); }}
                      style={btnSm(colors)}>Reload</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* MCP Servers */}
          {tab === "mcp" && (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 10 }}>MCP Servers ({mcpServers.length})</div>
              {mcpServers.length === 0 && <div style={{ color: colors.textSecondary }}>No MCP servers configured.</div>}
              {mcpServers.map((s) => (
                <div key={s.name} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "8px 12px", marginBottom: 6, borderRadius: 6,
                  border: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary,
                }}>
                  <div>
                    <span style={{ fontWeight: 600 }}>{s.name}</span>
                    <span style={{ marginLeft: 8, fontSize: 10, color: s.state === "running" ? "#22c55e" : colors.textSecondary }}>
                      {s.state}
                    </span>
                    {s.transport && <span style={{ marginLeft: 8, fontSize: 10, color: colors.textSecondary }}>{s.transport}</span>}
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={async () => { await api("/mcp/servers/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: s.name }) }); loadAll(); }}
                      style={btnSm(colors)}>Start</button>
                    <button onClick={async () => { await api(`/mcp/servers/${s.name}/stop`, { method: "POST" }); loadAll(); }}
                      style={btnSm(colors)}>Stop</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Processes */}
          {tab === "processes" && (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 10 }}>Managed Processes ({processes.length})</div>
              {processes.length === 0 && <div style={{ color: colors.textSecondary }}>No managed processes running.</div>}
              {processes.map((p) => (
                <div key={p.proc_id} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "8px 12px", marginBottom: 6, borderRadius: 6,
                  border: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary,
                }}>
                  <div>
                    <span style={{ fontWeight: 600 }}>{p.cmd}</span>
                    <span style={{ marginLeft: 8, fontSize: 10, color: p.status === "running" ? "#22c55e" : "#ef4444" }}>
                      {p.status}
                    </span>
                    {p.pid && <span style={{ marginLeft: 8, fontSize: 10, color: colors.textSecondary }}>PID {p.pid}</span>}
                  </div>
                  <button onClick={async () => { await api(`/processes/${p.proc_id}/kill`, { method: "POST" }); loadAll(); }}
                    style={{ ...btnSm(colors), background: "#ef4444", color: "#fff" }}>Kill</button>
                </div>
              ))}
            </div>
          )}

          {/* Browser */}
          {tab === "browser" && (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 10 }}>t("browserTab") Control</div>
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <input value={navUrl} onChange={(e: any) => setNavUrl(e.target.value)}
                  placeholder="https://example.com"
                  style={{
                    flex: 1, padding: "6px 10px", fontSize: 12, borderRadius: 6,
                    background: colors.bgSecondary, color: colors.text, border: `1px solid ${colors.border}`,
                    outline: "none",
                  }} />
                <button onClick={async () => {
                  const r = await api("/browser/navigate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: navUrl }) });
                  setBrowserMsg(r?.ok ? "Navigated" : "Failed"); loadPages();
                }} style={btnSm(colors)}>Navigate</button>
                <button onClick={async () => {
                  const r = await api("/browser/screenshot", { method: "POST" });
                  setBrowserMsg(r?.ok ? "Screenshot taken" : "Failed");
                }} style={btnSm(colors)}>📸</button>
              </div>
              {browserMsg && <div style={{ fontSize: 11, color: colors.textSecondary, marginBottom: 8 }}>{browserMsg}</div>}
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Open Pages</div>
              {pages.map((p) => (
                <div key={p.id} style={{
                  padding: "6px 10px", marginBottom: 4, borderRadius: 4,
                  border: `1px solid ${colors.border}`, fontSize: 11,
                }}>
                  <span style={{ fontWeight: 600 }}>{p.title || "(untitled)"}</span>
                  <span style={{ marginLeft: 8, color: colors.textSecondary }}>{p.url}</span>
                </div>
              ))}
              {pages.length === 0 && <div style={{ color: colors.textSecondary }}>No open pages.</div>}
            </div>
          )}

          {/* Approval */}
          {tab === "approval" && (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 10 }}>Approval System</div>
              <div style={{
                padding: "8px 12px", borderRadius: 6, marginBottom: 10,
                border: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary,
              }}>
                {t("mode")}: <span style={{ fontWeight: 600, color: colors.accent }}>{approvalStatus || "unknown"}</span>
              </div>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Pending ({approvalPending.length})</div>
              {approvalPending.length === 0 && <div style={{ color: colors.textSecondary }}>No pending approvals.</div>}
              {approvalPending.map((a: any, i: number) => (
                <div key={i} style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "8px 12px", marginBottom: 6, borderRadius: 6,
                  border: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary,
                }}>
                  <div>
                    <span style={{ fontWeight: 600 }}>{a.action || a.type || "Unknown"}</span>
                    <span style={{ marginLeft: 8, fontSize: 10, color: colors.textSecondary }}>
                      {a.description || a.tool || ""}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={async () => { await api("/approval/approve", { method: "POST" }); loadAll(); }}
                      style={{ ...btnSm(colors), background: "#22c55e", color: "#fff" }}>Approve</button>
                    <button onClick={async () => { await api("/approval/reject", { method: "POST" }); loadAll(); }}
                      style={{ ...btnSm(colors), background: "#ef4444", color: "#fff" }}>Reject</button>
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

function btnSm(colors: any): React.CSSProperties {
  return {
    padding: "4px 10px", fontSize: 11, borderRadius: 4, border: `1px solid ${colors.border}`,
    background: colors.bgSecondary || "#1a1a2e", color: colors.text, cursor: "pointer",
  };
}
