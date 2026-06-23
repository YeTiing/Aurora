"""RE Tool - reverse engineering operations: capture, deobfuscate, mine, analyze."""
import json
from backend.tools.base import ToolSpec, ToolCallResult

async def re_handler(action: str = "", session_id: str = "", url: str = "", file: str = "", code: str = "", question: str = "") -> ToolCallResult:
    """RE toolkit: capture, deobfuscate, mine, analyze, list-sessions, inspect"""
    try:
        if action == "capture-start":
            from backend.re_engine.capture import get_capture_engine
            eng = get_capture_engine()
            sess = eng.start_session(url or "")
            return ToolCallResult(success=True, output=f"RE session started: {sess.id}\nURL: {url or 'manual'}\nCapturing requests...")

        elif action == "capture-stop":
            from backend.re_engine.capture import get_capture_engine
            eng = get_capture_engine()
            stats = eng.stop_session()
            return ToolCallResult(success=True, output=json.dumps(stats, indent=2, ensure_ascii=False))

        elif action == "capture-request":
            from backend.re_engine.capture import get_capture_engine
            eng = get_capture_engine()
            try:
                flow = json.loads(code) if code else {}
            except:
                return ToolCallResult(success=False, output="", error="Invalid JSON in 'code'. Expect mitmproxy flow dict.")
            req = eng.capture_mitm_request(flow)
            return ToolCallResult(success=True, output=f"Captured: {req.method} {req.url[:120]}\nID: {req.id}\nStatus: {req.response_status}")

        elif action == "deobfuscate":
            from backend.re_engine.deobfuscator import get_deobfuscator
            d = get_deobfuscator()
            if file:
                from pathlib import Path
                p = Path(file)
                if p.exists():
                    code = p.read_text(encoding="utf-8", errors="ignore")
                else:
                    return ToolCallResult(success=False, output="", error=f"File not found: {file}")
            if not code:
                return ToolCallResult(success=False, output="", error="Provide 'file' or 'code'")
            result = d.analyze(code, file or "inline.js")
            report = d.to_report(result)
            return ToolCallResult(success=True, output=report)

        elif action == "mine":
            from backend.re_engine.miner import get_api_miner
            m = get_api_miner()
            results = []
            if session_id:
                results = m.mine_from_session(session_id)
            elif file:
                results = m.mine_file(file)
            elif code:
                results = m.mine_text(code, "inline")
            else:
                return ToolCallResult(success=False, output="", error="Provide 'session_id', 'file', or 'code'")
            if not results:
                return ToolCallResult(success=True, output="No API endpoints found.")
            lines = [f"API Endpoints Found: {len(results)}", ""]
            for r in results[:30]:
                lines.append(f"  [{r['category']}] {r['url']}  @ {r.get('source','')}")
            return ToolCallResult(success=True, output="\n".join(lines))

        elif action == "analyze":
            from backend.re_engine.analyzer import get_analyzer
            a = get_analyzer()
            if session_id:
                result = a.analyze_session(session_id)
            elif code:
                result = {
                    "scene": a.detect_scene(url or "", "", code),
                    "auth": a.trace_auth("", code),
                    "crypto": a.fingerprint_crypto(code),
                }
            else:
                return ToolCallResult(success=False, output="", error="Provide 'session_id' or 'code'+'url'")
            return ToolCallResult(success=True, output=json.dumps(result, indent=2, ensure_ascii=False))

        elif action == "list-sessions":
            from backend.re_engine.session import get_re_manager
            mgr = get_re_manager()
            sessions = mgr.list_sessions()
            if not sessions:
                return ToolCallResult(success=True, output="No RE sessions. Start capture first.")
            lines = [f"RE Sessions: {len(sessions)}", ""]
            for s in sessions:
                lines.append(f"  {s['id']} | {s['url'][:60] or 'manual'} | {s['apis']} APIs | {s['js_files']} JS")
            return ToolCallResult(success=True, output="\n".join(lines))

        elif action == "inspect":
            from backend.re_engine.session import get_re_manager
            mgr = get_re_manager()
            sess = mgr.get(session_id)
            if not sess:
                return ToolCallResult(success=False, output="", error=f"Session '{session_id}' not found")
            reqs = sess.get_requests(api_only=True)
            if not reqs:
                return ToolCallResult(success=True, output=f"No API requests in session {session_id}")
            lines = [f"Session: {session_id} | {len(reqs)} API requests", ""]
            for r in reqs[:25]:
                lines.append(f"  #{r.get('seq')} {r.get('method')} {r.get('path','')[:80]} {r.get('response_status')}")
            return ToolCallResult(success=True, output="\n".join(lines))

        else:
            return ToolCallResult(success=False, output="", error=f"Unknown action: {action}. Use: capture-start, capture-stop, capture-request, deobfuscate, mine, analyze, list-sessions, inspect")

    except Exception as e:
        return ToolCallResult(success=False, output="", error=f"{type(e).__name__}: {str(e)[:300]}")


RE_SPEC = ToolSpec(
    name="re",
    description=(
        "Reverse engineering toolkit. "
        "Actions: capture-start (begin traffic capture), capture-stop, capture-request (add flow), "
        "deobfuscate (analyze JS: find crypto, detect obfuscation, extract APIs), "
        "mine (extract API endpoints from session/code), "
        "analyze (scene detection + auth chain + crypto fingerprint), "
        "list-sessions, inspect (view captured API requests)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "RE action", "enum": ["capture-start", "capture-stop", "capture-request", "deobfuscate", "mine", "analyze", "list-sessions", "inspect"]},
            "session_id": {"type": "string", "description": "RE session ID"},
            "url": {"type": "string", "description": "Target URL"},
            "file": {"type": "string", "description": "JS file path to analyze"},
            "code": {"type": "string", "description": "JS code or JSON flow data"},
            "question": {"type": "string", "description": "Analysis question"},
        },
        "required": ["action"],
    },
)
