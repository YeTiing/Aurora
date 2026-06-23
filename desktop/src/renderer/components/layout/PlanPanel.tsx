import React from "react";
import { useStore } from "../../store";
import type { PlanStep } from "../../../shared/types";

export function PlanPanel({ plan }: { plan: PlanStep[] }) {
    const colors = useStore((s) => s.themeColors);

    const statusIcon = (status: PlanStep["status"]) => {
        switch (status) {
            case "completed": return "✅";
            case "in_progress": return "🔄";
            case "failed": return "❌";
            default: return "○";
        }
    };

    return (
        <div style={{ padding: "8px 12px", fontSize: 12 }}>
            <div style={{ fontWeight: 600, color: colors.textSecondary, fontSize: 11, textTransform: "uppercase", marginBottom: 8 }}>
                Plan
            </div>
            {plan.map((step, i) => (
                <div key={i} style={{
                    display: "flex", gap: 6, padding: "3px 0", alignItems: "flex-start",
                    color: step.status === "in_progress" ? colors.accent : colors.textSecondary,
                    opacity: step.status === "completed" ? 0.7 : 1,
                }}>
                    <span style={{ flexShrink: 0 }}>{statusIcon(step.status)}</span>
                    <span style={{ textDecoration: step.status === "completed" ? "line-through" : "none" }}>
                        {step.description}
                    </span>
                </div>
            ))}
        </div>
    );
}