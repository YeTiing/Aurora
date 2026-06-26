"""Session Export - generate clean Markdown from Aurora sessions.

Exports: conversation, plan steps, tool call summaries, final results.
"""
from __future__ import annotations
import time, json, zipfile, io, gzip
from enum import Enum
from dataclasses import dataclass
from typing import Any


class ExportFormat(Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    HTML = "html"


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

    if cfg.include_plan and plan:
        lines.append("## Plan")
        lines.append("")
        icons = {"pending": "⏳", "in_progress": "🔧", "completed": "✅", "failed": "❌", "skipped": "⚪️"}
        for step in plan:
            icon = icons.get(step.get("status", "pending"), "⏳")
            desc = step.get("description", "")
            result = step.get("result", "")
            lines.append(f"- {icon} **Step {step.get('step', '?')}:** {desc}")
            if result:
                lines.append(f"  - Result: {result[:300]}")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Conversation")
    lines.append("")
    role_labels_md = {"user": "💬 You", "assistant": "✅ Aurora", "tool": "🔧 Tool", "system": "📦 System"}
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "").strip()
        ts = msg.get("timestamp", 0)

        if role == "system" and not cfg.include_system_messages:
            continue
        if not content and role != "tool":
            continue

        role_label = role_labels_md.get(role, f"❓ {role}")
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
                content = content[:cfg.max_tool_output_chars] + "\n\n*[... " + str(len(content) - cfg.max_tool_output_chars) + " more chars truncated]*"
            lines.append("```")
            lines.append(content[:8000])
            lines.append("```")
        else:
            if len(content) > 500:
                lines.append(content)
            else:
                lines.append(content)
        lines.append("")

    if cfg.include_tool_calls and tool_logs:
        lines.append("---")
        lines.append("")
        lines.append("## Tool Calls Summary")
        lines.append("")
        lines.append("| # | Tool | Success | Output Length |")
        lines.append("|---|---|---|---|")
        for i, tl in enumerate(tool_logs):
            name = tl.get("tool", tl.get("toolName", "?"))
            success = "✅" if tl.get("success", True) else "❌"
            output_len = len(tl.get("output", tl.get("result", "")))
            lines.append(f"| {i+1} | `{name}` | {success} | {output_len:,} chars |")
        lines.append("")

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


def export_session_html(session_data: dict, config: ExportConfig | None = None) -> str:
    """Generate a self-contained HTML page with dark theme styling."""
    cfg = config or ExportConfig()
    title = session_data.get("title", "Untitled Session")
    workspace = session_data.get("workspace", ".")
    created = session_data.get("createdAt", time.time())
    messages: list[dict] = session_data.get("messages", [])
    plan: list[dict] = session_data.get("plan", [])
    tool_logs: list[dict] = session_data.get("toolLogs", [])

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    plan_rows = ""
    if cfg.include_plan and plan:
        icons = {"pending": "⏳", "in_progress": "🔧", "completed": "✅", "failed": "❌", "skipped": "⚪"}
        for step in plan:
            icon = icons.get(step.get("status", "pending"), "⏳")
            desc = esc(step.get("description", ""))
            st = step.get("step", "?")
            result = esc((step.get("result", "") or "")[:200])
            plan_rows += "<tr><td>" + icon + "</td><td>" + esc(str(st)) + "</td><td>" + desc + "</td><td>" + result + "</td></tr>\n"

    msg_html = ""
    role_labels = {"user": "👤 You", "assistant": "✅ Aurora", "tool": "🔧 Tool", "system": "📦 System"}
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = (msg.get("content", "") or "").strip()
        ts = msg.get("timestamp", 0)
        if role == "system" and not cfg.include_system_messages:
            continue
        if not content and role != "tool":
            continue
        label = role_labels.get(role, "❓ " + role)
        ts_str = " <span class=\"ts\">" + _fmt_time_short(ts) + "</span>" if cfg.include_timestamps else ""
        content_html = esc(content[:8000]).replace("\n", "<br>")
        if role == "tool":
            tool_log = _find_tool_log(tool_logs, msg)
            tool_info = ""
            if tool_log:
                name = esc(tool_log.get("tool", "?"))
                ok = "✅" if tool_log.get("success", True) else "❌"
                tool_info = "<div class=\"tool-info\"><strong>Tool:</strong> <code>" + name + "</code> " + ok + "</div>"
            msg_html += "<div class=\"msg tool\"><div class=\"role\">" + label + ts_str + "</div>" + tool_info + "<pre>" + content_html + "</pre></div>\n"
        else:
            msg_html += "<div class=\"msg " + role + "\"><div class=\"role\">" + label + ts_str + "</div><div class=\"body\">" + content_html + "</div></div>\n"

    tool_summary_rows = ""
    if cfg.include_tool_calls and tool_logs:
        for i, tl in enumerate(tool_logs):
            name = esc(tl.get("tool", tl.get("toolName", "?")))
            ok = "✅" if tl.get("success", True) else "❌"
            out_len = len((tl.get("output", tl.get("result", "")) or ""))
            tool_summary_rows += "<tr><td>" + str(i+1) + "</td><td><code>" + name + "</code></td><td>" + ok + "</td><td>" + str(out_len) + "</td></tr>\n"

    plan_section = ""
    if cfg.include_plan and plan:
        plan_section = "<section class=\"plan-section\"><h2>📌 Plan</h2><table><thead><tr><th></th><th>#</th><th>Description</th><th>Result</th></tr></thead><tbody>" + plan_rows + "</tbody></table></section>"

    tool_section = ""
    if cfg.include_tool_calls and tool_logs:
        tool_section = "<section class=\"tool-section\"><h2>🔧 Tool Calls (" + str(len(tool_logs)) + ")</h2><table><thead><tr><th>#</th><th>Tool</th><th>OK</th><th>Output</th></tr></thead><tbody>" + tool_summary_rows + "</tbody></table></section>"

    exported_at = _fmt_time(time.time())

    # Build HTML using string concatenation
    h = []
    h.append("<!DOCTYPE html>")
    h.append("<html lang=\"en\">")
    h.append("<head>")
    h.append("<meta charset=\"utf-8\">")
    h.append("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    h.append("<title>" + esc(title) + " — Aurora Export</title>")
    h.append("<style>")
    h.append("*{box-sizing:border-box;margin:0;padding:0}")
    h.append("body{background:#1a1a2e;color:#e0e0e0;font-family:system-ui,-apple-system,sans-serif;line-height:1.6;max-width:900px;margin:0 auto;padding:2rem}")
    h.append("h1{color:#e94560;border-bottom:2px solid #e94560;padding-bottom:.5rem;margin-bottom:1rem}")
    h.append("h2{color:#f0a500;margin:2rem 0 1rem}")
    h.append(".meta{color:#888;font-size:.85rem;margin-bottom:2rem}")
    h.append(".msg{background:#16213e;border-radius:8px;padding:1rem;margin-bottom:1rem;border-left:3px solid #533483}")
    h.append(".msg.user{border-left-color:#0f3460}")
    h.append(".msg.tool{border-left-color:#f0a500}")
    h.append(".msg.system{border-left-color:#555}")
    h.append(".role{font-weight:700;margin-bottom:.5rem}")
    h.append(".role .ts{font-weight:400;color:#666;margin-left:.5rem}")
    h.append(".tool-info{font-size:.85rem;color:#aaa;margin-bottom:.5rem}")
    h.append(".tool-info code{background:#333;padding:2px 6px;border-radius:4px}")
    h.append("pre{background:#0d1117;padding:1rem;border-radius:6px;overflow-x:auto;font-size:.85rem;max-height:400px;overflow-y:auto}")
    h.append("table{width:100%;border-collapse:collapse;margin-bottom:1rem}")
    h.append("th,td{padding:.5rem .75rem;text-align:left;border-bottom:1px solid #333}")
    h.append("th{background:#533483;color:#fff}")
    h.append("tr:nth-child(even){background:#16213e}")
    h.append("code{background:#333;padding:1px 5px;border-radius:3px;font-size:.9em}")
    h.append(".footer{margin-top:3rem;padding-top:1rem;border-top:1px solid #333;color:#666;font-size:.85rem}")
    h.append("</style>")
    h.append("</head>")
    h.append("<body>")
    h.append("<h1>" + esc(title) + "</h1>")
    h.append("<div class=\"meta\">Workspace: <code>" + esc(workspace) + "</code> &middot; Created: " + _fmt_time(created) + " &middot; Messages: " + str(len(messages)) + " &middot; Tools: " + str(len(tool_logs)) + "</div>")
    h.append(plan_section)
    h.append("<section class=\"conv-section\">")
    h.append("<h2>💬 Conversation</h2>")
    h.append(msg_html)
    h.append("</section>")
    h.append(tool_section)
    h.append("<div class=\"footer\">Exported by Aurora on " + exported_at + "</div>")
    h.append("</body>")
    h.append("</html>")
    return "\n".join(h)


def export_sessions_batch(session_ids: list[str], fmt: str = "markdown") -> bytes:
    """Export multiple sessions as a zip archive. Each session gets its own file."""
    from backend.session_rollout import RolloutReader

    exporters = {"markdown": export_session, "json": export_session_json, "html": export_session_html}
    exporter = exporters.get(fmt)
    if not exporter:
        raise ValueError("Unsupported format: " + fmt + ". Use markdown, json, or html.")

    ext = {"markdown": ".md", "json": ".json", "html": ".html"}[fmt]
    buf = io.BytesIO()
    all_sessions = RolloutReader.list_sessions()
    session_map = {s.get("session_id", ""): s for s in all_sessions}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for sid in session_ids:
            s = session_map.get(sid)
            if not s:
                continue
            title = s.get("title", sid)
            safe = "".join(c for c in title if c.isalnum() or c in " _-.").strip()[:60] or sid[:8]
            filename = safe + ext
            content = exporter(s)
            zf.writestr(filename, content.encode("utf-8"))

    buf.seek(0)
    return buf.getvalue()


def build_session_from_rollout(filepath: str) -> dict:
    """Read a rollout JSONL file and reconstruct session data for export."""
    import json as _json

    session_data = {
        "title": "Untitled Session",
        "workspace": ".",
        "createdAt": time.time(),
        "updatedAt": time.time(),
        "messages": [],
        "plan": [],
        "toolLogs": [],
    }

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            events = []
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
    except (FileNotFoundError, OSError):
        return session_data

    seen_tool_ids = set()

    for ev in events:
        etype = ev.get("type", "")
        pl = ev.get("payload", {})
        ts = ev.get("ts", time.time())

        if etype == "session_meta":
            session_data["title"] = pl.get("title", pl.get("id", "Untitled Session"))
            session_data["workspace"] = pl.get("cwd", ".")
            session_data["createdAt"] = ts
            session_data["updatedAt"] = ts

        elif etype == "response_item":
            item_type = pl.get("type", "")
            if item_type == "message":
                session_data["messages"].append({
                    "id": pl.get("id", ""),
                    "role": pl.get("role", "assistant"),
                    "content": pl.get("content", pl.get("text", "")),
                    "timestamp": ts,
                })
                session_data["updatedAt"] = ts
            elif item_type == "function_call":
                tool_id = pl.get("call_id", pl.get("id", ""))
                session_data["toolLogs"].append({
                    "tool": pl.get("name", "unknown"),
                    "toolCallId": tool_id,
                    "success": not (pl.get("output", "") or "").startswith("Error:"),
                    "output": pl.get("output", pl.get("result", "")),
                    "input": pl.get("arguments", pl.get("input", "")),
                })
                session_data["updatedAt"] = ts
                seen_tool_ids.add(tool_id)
            elif item_type == "tool_result":
                tc_id = pl.get("tool_call_id", pl.get("call_id", ""))
                session_data["messages"].append({
                    "id": tc_id,
                    "role": "tool",
                    "content": pl.get("output", pl.get("content", "")),
                    "timestamp": ts,
                })

        elif etype == "turn_context":
            session_data["messages"].append({
                "id": pl.get("id", ""),
                "role": "user",
                "content": pl.get("user_message", pl.get("query", pl.get("message", ""))),
                "timestamp": ts,
            })
            session_data["updatedAt"] = ts

        elif etype == "plan":
            session_data["plan"] = pl.get("steps", pl.get("plan", []))
            session_data["updatedAt"] = ts

        elif etype == "event_msg" and pl.get("type") == "token_count":
            info = pl.get("info", {})
            usage = info.get("total_token_usage", {})
            session_data["tokenUsage"] = {
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "total": usage.get("total_tokens", 0),
            }

    return session_data


def _find_tool_log(tool_logs: list[dict], msg: dict) -> dict | None:
    for tl in tool_logs:
        if tl.get("toolCallId") == msg.get("id"):
            return tl
    return None


def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(max(ts, 0)))


def _fmt_time_short(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(max(ts, 0)))