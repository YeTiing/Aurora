import React, { useEffect, useState } from "react";
import { useStore } from "../../store";

interface MemoryStats {
  agent_memory: { entries: number; chars: number; usage_pct: number };
  user_profile: { entries: number; chars: number; usage_pct: number };
  curator: { runs: number; paused: boolean };
  skills: { total: number; active: number; archived: number };
  honcho: { turns: number; traits: number };
  fts5: { sessions: number };
}

interface HonchoData {
  traits: string[];
  preferences: string[];
  context: string;
}

interface CronTask {
  name: string;
  schedule: string;
  prompt: string;
  enabled: boolean;
  run_count: number;
  next_run: number;
}

export function MemoryDashboard() {
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [honcho, setHoncho] = useState<HonchoData | null>(null);
  const [cronTasks, setCronTasks] = useState<CronTask[]>([]);
  const [soul, setSoul] = useState("");
  const [editingSoul, setEditingSoul] = useState(false);
  const [activeTab, setActiveTab] = useState<"overview" | "skills" | "soul" | "cron">("overview");
  const colors = useStore((s) => s.themeColors);

  const api = async (path: string) => {
    try {
      const r = await fetch(`http://127.0.0.1:9876${path}`);
      return await r.json();
    } catch { return null; }
  };

  useEffect(() => {
    const load = async () => {
      const [s, h, c, so] = await Promise.all([
        api("/memory/stats"),
        api("/memory/honcho"),
        api("/cron"),
        api("/soul"),
      ]);
      if (s) setStats(s);
      if (h) setHoncho(h);
      if (Array.isArray(c)) setCronTasks(c);
      if (so?.content) setSoul(so.content);
    };
    load();
    const int = setInterval(load, 5000);
    return () => clearInterval(int);
  }, []);

  const saveSoul = async () => {
    await fetch("http://127.0.0.1:9876/soul", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: soul }),
    });
    setEditingSoul(false);
  };

  const bar = (pct: number, color: string) => (
    <div style={{ height: 6, background: colors.border || "#333", borderRadius: 3, marginTop: 4 }}>
      <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 3, transition: "width 0.5s" }} />
    </div>
  );

  const tabStyle = (tab: string) => ({
    padding: "4px 12px",
    cursor: "pointer",
    borderBottom: activeTab === tab ? `2px solid ${colors.accent || "#8b5cf6"}` : "2px solid transparent",
    color: activeTab === tab ? (colors.accent || "#8b5cf6") : (colors.text || "#ccc"),
    fontSize: 12,
    fontWeight: activeTab === tab ? 600 : 400,
  } as React.CSSProperties);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", color: colors.text || "#ccc", fontSize: 12 }}>
      {/* Tabs */}
      <div style={{ display: "flex", gap: 8, padding: "4px 12px", borderBottom: `1px solid ${colors.border || "#333"}` }}>
        <div style={tabStyle("overview")} onClick={() => setActiveTab("overview")}>Memory</div>
        <div style={tabStyle("skills")} onClick={() => setActiveTab("skills")}>Skills</div>
        <div style={tabStyle("cron")} onClick={() => setActiveTab("cron")}>Cron</div>
        <div style={tabStyle("soul")} onClick={() => setActiveTab("soul")}>SOUL</div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 12 }}>
        {activeTab === "overview" && stats && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {/* Agent Memory */}
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Agent Memory</div>
              <div>{stats.agent_memory.entries} entries · {stats.agent_memory.chars}/{2200} chars</div>
              {bar(stats.agent_memory.usage_pct, "#8b5cf6")}
            </div>

            {/* User Profile */}
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>User Profile</div>
              <div>{stats.user_profile.entries} entries · {stats.user_profile.chars}/{1375} chars</div>
              {bar(stats.user_profile.usage_pct, "#06b6d4")}
            </div>

            {/* Honcho */}
            {honcho && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>User Model (Honcho)</div>
                {honcho.traits.length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {honcho.traits.map((t, i) => (
                      <span key={i} style={{ padding: "2px 8px", borderRadius: 10, background: colors.accent + "22" || "#8b5cf622", fontSize: 11 }}>{t}</span>
                    ))}
                  </div>
                )}
                {honcho.preferences.length > 0 && (
                  <div style={{ marginTop: 4 }}>
                    {honcho.preferences.map((p, i) => (
                      <div key={i} style={{ color: colors.text + "aa" || "#999", fontSize: 11 }}>· {p}</div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Sessions */}
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Sessions</div>
              <div>{stats.fts5.sessions} indexed · Curator runs: {stats.curator.runs}</div>
            </div>
          </div>
        )}

        {activeTab === "skills" && (
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>Skills</div>
            {stats ? (
              <div>
                <div>Active: {stats.skills.active} · Archived: {stats.skills.archived} · Total: {stats.skills.total}</div>
                {stats.skills.total === 0 && <div style={{ marginTop: 8, opacity: 0.6 }}>No skills yet. Agent creates them automatically from complex tasks, or use /skill-create.</div>}
              </div>
            ) : <div>Loading...</div>}
          </div>
        )}

        {activeTab === "cron" && (
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>Cron Tasks</div>
            {cronTasks.length === 0 && <div style={{ opacity: 0.6 }}>No scheduled tasks. Use "cron add" or ask the agent to schedule something.</div>}
            {cronTasks.map((t, i) => (
              <div key={i} style={{ padding: "8px 0", borderBottom: `1px solid ${colors.border + "44" || "#33333344"}` }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ fontWeight: 600 }}>{t.enabled ? "▶" : "⏸"} {t.name}</span>
                  <span style={{ fontSize: 11, opacity: 0.6 }}>runs: {t.run_count}</span>
                </div>
                <div style={{ fontSize: 11, opacity: 0.7 }}>{t.schedule}</div>
                <div style={{ fontSize: 11, opacity: 0.5, marginTop: 2 }}>{t.prompt?.slice(0, 100)}</div>
              </div>
            ))}
          </div>
        )}

        {activeTab === "soul" && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontWeight: 600 }}>SOUL.md</span>
              <button
                onClick={() => editingSoul ? saveSoul() : setEditingSoul(true)}
                style={{
                  padding: "2px 10px", borderRadius: 4, border: "none", cursor: "pointer",
                  background: colors.accent || "#8b5cf6", color: "#fff", fontSize: 11,
                }}
              >
                {editingSoul ? "Save" : "Edit"}
              </button>
            </div>
            {editingSoul ? (
              <textarea
                value={soul}
                onChange={(e) => setSoul(e.target.value)}
                style={{
                  width: "100%", height: 300, background: "#1a1a2e", color: "#ccc",
                  border: `1px solid ${colors.border || "#333"}`, borderRadius: 4, padding: 8,
                  fontFamily: "monospace", fontSize: 11, resize: "vertical",
                }}
              />
            ) : (
              <pre style={{ whiteSpace: "pre-wrap", fontSize: 11, lineHeight: 1.5, margin: 0 }}>{soul || "(no SOUL.md)"}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
