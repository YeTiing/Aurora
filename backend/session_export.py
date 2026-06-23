"""Session Export - generate clean Markdown from Aurora sessions.

Exports: conversation, plan steps, tool call summaries, final results.
"""
from __future__ import annotations
import time, json
from dataclasses import dataclass
from typing import Any

@dataclass
class ExportConfig:
    include_tool_calls: bool = True
    include_plan: bool = True
    include_timestamps: bool = True
    include_system_messages: bool = False
    max_tool_output_chars: int = 2000
    language: str = "zh"

def export_session(session_data: dict, config: ExportConfig | None = None) -> str:
    """Generate Markdown from session data."""
    cfg = config or ExportConfig()
    lines: list[str] = []

    # Header
    title = session_data.get("title", "Untitled Session")
    workspace = session_data.get("workspace", ".")
    created = session_data.get("createdAt", time.time())
    updated = session_data.get("updatedAt", time.time())
    messages: list[dict] = session_data.get("messages", [])
    plan: list[dict] = session_data.get("plan", [])
    tool_logs: list[dict] = session_data.get("toolLogs", [])

    lines.append(f"# {title}")
    lines.append("")
    if cfg.include_timestamps:
        lines.append(f"> **Workspace:** `{workspace}` | **Created:** {_fmt_time(created)} | **Updated:** {_fmt_time(updated)}")
    lines.append(f"> **Messages:** {len(messages)} | **Tools:** {len(tool_logs)} | **Plan steps:** {len(plan)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Plan section
    if cfg.include_plan and plan:
        lines.append("## Plan")
        lines.append("")
        for step in plan:
            status_icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(step.get("status", "pending"), "⬜")
            desc = step.get("description", "")
            result = step.get("result", "")
            lines.append(f"- {status_icon} **Step {step.get('step', '?')}:** {desc}")
            if result: lines.append(f"  - Result: {result[:300]}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Messages
    lines.append("## Conversation")
    lines.append("")
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "").strip()
        ts = msg.get("timestamp", 0)
        msg_id = msg.get("id", f"msg{i}")

        if role == "system" and not cfg.include_system_messages:
            continue
        if not content and role != "tool":
            continue

        role_label = {"user": "🧑 You", "assistant": "✨ Aurora", "tool": "🔧 Tool", "system": "📋 System"}.get(role, f"❓ {role}")
        ts_str = f" `{_fmt_time_short(ts)}`" if cfg.include_timestamps else ""

        lines.append(f"### {role_label}{ts_str}")
        lines.append("")

        if role == "tool":
            tool_log = _find_tool_log(tool_logs, msg)
            if tool_log:
                tool_name = tool_log.get("tool", "unknown")
                success = tool_log.get("success", True)
                lines.append(f"**Tool:** `{tool_name}` {'✅' if success else '❌'}")

            if len(content) > cfg.max_tool_output_chars:
                content = content[:cfg.max_tool_output_chars] + f"\n\n*[... {len(content) - cfg.max_tool_output_chars} more chars truncated]*"
            lines.append("```")
            lines.append(content[:8000])
            lines.append("```")
        else:
            # For assistant/user, render as blockquote for long messages
            if len(content) > 500:
                lines.append(content)
            else:
                lines.append(content)

        lines.append("")

    # Tool summary
    if cfg.include_tool_calls and tool_logs:
        lines.append("---")
        lines.append("")
        lines.append("## Tool Calls Summary")
        lines.append("")
        lines.append(f"| # | Tool | Success | Output Length |")
        lines.append(f"|---|---|---|---|")
        for i, tl in enumerate(tool_logs):
            name = tl.get("tool", tl.get("toolName", "?"))
            success = "✅" if tl.get("success", True) else "❌"
            output_len = len(tl.get("output", tl.get("result", "")))
            lines.append(f"| {i+1} | `{name}` | {success} | {output_len:,} chars |")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append(f"*Exported by Aurora AI Agent on {_fmt_time(time.time())}*")
    lines.append("")

    return "\n".join(lines)

def export_session_json(session_data: dict) -> str:
    """Export as a structured JSON report (machine-readable)."""
    messages: list[dict] = session_data.get("messages", [])
    plan: list[dict] = session_data.get("plan", [])
    tool_logs: list[dict] = session_data.get("toolLogs", [])

    report = {
        "title": session_data.get("title", ""),
        "workspace": session_data.get("workspace", "."),
        "exported_at": time.time(),
        "stats": {
            "messages": len(messages),
            "tool_calls": len(tool_logs),
            "plan_steps": len(plan),
            "plan_completed": sum(1 for p in plan if p.get("status") == "completed"),
            "tools_succeeded": sum(1 for t in tool_logs if t.get("success", True)),
            "tools_failed": sum(1 for t in tool_logs if not t.get("success", True)),
        },
        "plan": plan,
        "tool_logs": [{"tool": t.get("tool", t.get("toolName")), "success": t.get("success", True), "output": (t.get("output", "") or "")[:2000]} for t in tool_logs],
        "conversation": [{"role": m.get("role"), "content": m.get("content", "")[:5000], "timestamp": m.get("timestamp")} for m in messages],
    }
    return json.dumps(report, indent=2, ensure_ascii=False)

def _find_tool_log(tool_logs: list[dict], msg: dict) -> dict | None:
    for tl in tool_logs:
        if tl.get("toolCallId") == msg.get("id"):
            return tl
    return None

def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(max(ts, 0)))

def _fmt_time_short(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(max(ts, 0)))
