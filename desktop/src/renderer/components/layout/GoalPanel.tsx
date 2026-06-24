import React, { useEffect, useState } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";

const API = "http://127.0.0.1:9876";

interface Goal {
  goal_id: string; objective: string; status: string;
  token_budget?: number; token_used?: number; created_at?: number;
}

export function GoalPanel({ onClose }: { onClose: () => void }) {
  const colors = useStore((s) => s.themeColors);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [budget, setBudget] = useState<any>(null);
  const [newObjective, setNewObjective] = useState("");
  const [newBudget, setNewBudget] = useState("");
  const [msg, setMsg] = useState("");

  const load = async () => {
    try {
      const [g, b] = await Promise.all([
        fetch(API + "/goal").then(r => r.json()),
        fetch(API + "/context/budget").then(r => r.json()),
      ]);
      if (Array.isArray(g)) setGoals(g); else if (g.goals) setGoals(g.goals);
      if (b) setBudget(b);
    } catch {}
  };

  useEffect(() => { load(); const i = setInterval(load, 8000); return () => clearInterval(i); }, []);

  const createGoal = async () => {
    const body: any = { objective: newObjective };
    if (newBudget) body.token_budget = parseInt(newBudget);
    await fetch(API + "/goal", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    setNewObjective(""); setNewBudget(""); setMsg("Goal created!");
    load(); setTimeout(() => setMsg(""), 2000);
  };

  const pct = budget ? Math.round((budget.token_usage || 0) / Math.max(budget.token_budget || 1, 1) * 100) : 0;
  const barColor = pct > 85 ? "#ef4444" : pct > 60 ? "#f59e0b" : "#22c55e";

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 2000, display: "flex", alignItems: "center", justifyContent: "center", backgroundColor: "rgba(0,0,0,0.5)", backdropFilter: "blur(2px)" }}>
      <div style={{ width: 560, maxHeight: "80vh", backgroundColor: colors.bg, borderRadius: 12, border: `1px solid ${colors.border}`, display: "flex", flexDirection: "column", boxShadow: "0 20px 60px rgba(0,0,0,0.5)", overflow: "hidden" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 16px", borderBottom: `1px solid ${colors.border}` }}>
          <span style={{ fontWeight: 700, fontSize: 15 }}>🎯 Goal Tracker</span>
          <button onClick={onClose} style={{ background: "none", border: "none", color: colors.textSecondary, fontSize: 18, cursor: "pointer" }}>✕</button>
        </div>
        <div style={{ flex: 1, overflow: "auto", padding: 16, fontSize: 12 }}>
          {budget && (
            <div style={{ padding: 12, borderRadius: 8, marginBottom: 16, border: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>{t("tokenBudgetLabel")}</div>
              <div style={{ display: "flex", gap: 12, marginBottom: 6 }}>
                <span style={{ color: colors.textSecondary }}>{t("used")}: <b style={{ color: colors.text }}>{(budget.token_usage || 0).toLocaleString()}</b></span>
                <span style={{ color: colors.textSecondary }}>{t("budget")}: <b style={{ color: colors.text }}>{(budget.token_budget || 0).toLocaleString()}</b></span>
                <span style={{ color: barColor, fontWeight: 600 }}>{pct}%</span>
              </div>
              <div style={{ height: 8, backgroundColor: colors.border, borderRadius: 4, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${Math.min(pct, 100)}%`, backgroundColor: barColor, borderRadius: 4, transition: "width 0.5s" }} />
              </div>
            </div>
          )}
          <div style={{ padding: 12, borderRadius: 8, marginBottom: 16, border: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>{t("newSession")}</div>
            <input value={newObjective} onChange={(e: any) => setNewObjective(e.target.value)} placeholder={t("typeObjective")} style={{ width: "100%", padding: "6px 10px", fontSize: 12, borderRadius: 6, background: colors.bg, color: colors.text, border: `1px solid ${colors.border}`, outline: "none", boxSizing: "border-box" as const }} />
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <input value={newBudget} onChange={(e: any) => setNewBudget(e.target.value)} placeholder={t("typeTokenBudget")} style={{ flex: 1, padding: "6px 10px", fontSize: 12, borderRadius: 6, background: colors.bg, color: colors.text, border: `1px solid ${colors.border}`, outline: "none" }} />
              <button onClick={createGoal} style={{ padding: "4px 12px", fontSize: 11, borderRadius: 4, border: "none", cursor: "pointer", background: colors.accent || "#8b5cf6", color: "#fff" }}>{t("newSession")}</button>
            </div>
            {msg && <div style={{ marginTop: 6, color: "#22c55e", fontSize: 11 }}>{msg}</div>}
          </div>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>{t("noActiveGoals")} ({goals.length})</div>
          {goals.length === 0 && <div style={{ color: colors.textSecondary }}>{t("noActiveGoals")}</div>}
          {goals.map((g) => (
            <div key={g.goal_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 12px", marginBottom: 6, borderRadius: 6, border: `1px solid ${colors.border}`, backgroundColor: colors.bgSecondary }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600 }}>{g.objective}</div>
                <div style={{ fontSize: 10, color: colors.textSecondary, marginTop: 2 }}>
                  {t("status")}: <span style={{ color: g.status === "active" ? "#22c55e" : colors.textSecondary }}>{g.status}</span>
                  {g.token_used !== undefined && <span style={{ marginLeft: 12 }}>Tokens: {g.token_used}/{g.token_budget || "\u221E"}</span>}
                </div>
              </div>
              <button onClick={async () => { await fetch(API + `/goal/${g.goal_id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "complete" }) }); load(); }} style={{ padding: "4px 12px", fontSize: 11, borderRadius: 4, border: "none", cursor: "pointer", background: "#22c55e", color: "#fff", marginLeft: 8 }}>{t("done")}</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
