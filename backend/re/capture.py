"""RE Capture Engine - mitmproxy hook + browser CDP traffic capture."""
from __future__ import annotations
import json, time, uuid, asyncio, threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .session import RESession, CapturedRequest, is_static_url, is_js_url, get_re_manager


class CaptureEngine:
    """Self-contained capture engine: mitm mode or CDP browser mode."""

    def __init__(self):
        self._mgr = get_re_manager()
        self._active_session: RESession | None = None
        self._mitm_running = False
        self._cdp_running = False
        self._hook_scripts: dict[str, str] = {}
        self._load_builtin_hooks()

    def _load_builtin_hooks(self):
        """Load JS hook scripts for injection."""
        self._hook_scripts["fetch"] = """
(function(){var _f=window.fetch;window.fetch=function(){
var a=arguments;window.__aurora_hooks=window.__aurora_hooks||[];
window.__aurora_hooks.push({t:'fetch',f:'fetch',a:JSON.stringify({url:a[0]&&a[0].url||a[0],
method:a[1]&&a[1].method||'GET'}),ts:Date.now()});
return _f.apply(this,a);}})();
"""
        self._hook_scripts["xhr"] = """
(function(){var _open=XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open=function(m,u){this.__aurora_url=u;this.__aurora_method=m;
window.__aurora_hooks=window.__aurora_hooks||[];
window.__aurora_hooks.push({t:'xhr',f:'open',a:JSON.stringify({url:u,method:m}),ts:Date.now()});
return _open.apply(this,arguments);};
var _send=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.send=function(b){
window.__aurora_hooks=window.__aurora_hooks||[];
window.__aurora_hooks.push({t:'xhr',f:'send',a:JSON.stringify({body:String(b||'').slice(0,500)}),ts:Date.now()});
return _send.apply(this,arguments);};})();
"""
        self._hook_scripts["crypto"] = """
(function(){
var methods=['encrypt','decrypt','sign','verify','digest','importKey','exportKey','generateKey','deriveKey'];
var targets=[window.crypto&&window.crypto.subtle].filter(Boolean);
targets.forEach(function(t){methods.forEach(function(m){if(t[m]){
var orig=t[m].bind(t);t[m]=function(){window.__aurora_hooks=window.__aurora_hooks||[];
window.__aurora_hooks.push({t:'crypto',f:m,a:JSON.stringify(Array.from(arguments).map(String).join(',').slice(0,200)),ts:Date.now()});return orig.apply(this,arguments);}}})});})();
"""

    def start_session(self, url: str = "") -> RESession:
        self._active_session = self._mgr.create(url)
        return self._active_session

    def capture_mitm_request(self, flow_dict: dict):
        """Process a mitmproxy flow dict into session."""
        if not self._active_session:
            self.start_session()
        sess = self._active_session
        sess.seq += 1
        rid = f"req_{sess.seq:04d}"
        url = flow_dict.get("url", "")
        parsed = urlparse(url)
        ct = flow_dict.get("response", {}).get("headers", {}).get("content-type", "")
        req_body = flow_dict.get("request", {}).get("content", "")
        resp_body = flow_dict.get("response", {}).get("content", "")

        req = CapturedRequest(
            id=rid, session_id=sess.id, seq=sess.seq,
            method=flow_dict.get("request", {}).get("method", "GET"),
            url=url, host=parsed.netloc, path=parsed.path or "/",
            request_headers=json.dumps(flow_dict.get("request", {}).get("headers", {})),
            request_body=req_body[:50000] if req_body else "",
            response_status=flow_dict.get("response", {}).get("status_code", 0),
            response_headers=json.dumps(flow_dict.get("response", {}).get("headers", {})),
            response_body=resp_body[:50000] if resp_body else "",
            content_type=ct,
            is_static=is_static_url(url),
            is_js=is_js_url(url, ct),
            is_streaming="event-stream" in ct,
            captured_at=time.time(),
        )
        sess.add_request(req)
        return req

    def capture_cdp_request(self, request_data: dict, response_data: dict | None = None):
        """Process CDP Network.requestWillBeSent / responseReceived."""
        if not self._active_session: return None
        sess = self._active_session
        sess.seq += 1
        rid = f"req_{sess.seq:04d}"
        url = request_data.get("url", "")
        parsed = urlparse(url)
        ct = (response_data or {}).get("response", {}).get("headers", {}).get("Content-Type", "")

        req = CapturedRequest(
            id=rid, session_id=sess.id, seq=sess.seq,
            method=request_data.get("method", "GET"),
            url=url, host=parsed.netloc, path=parsed.path or "/",
            request_headers=json.dumps(dict(request_data.get("headers", {}))),
            request_body=request_data.get("postData", "")[:50000],
            response_status=(response_data or {}).get("response", {}).get("status", 0),
            response_headers=json.dumps(dict((response_data or {}).get("response", {}).get("headers", {}))),
            response_body="",
            content_type=ct,
            is_static=is_static_url(url),
            is_js=is_js_url(url, ct),
            is_streaming="event-stream" in ct,
            captured_at=time.time(),
        )
        sess.add_request(req)
        return req

    def record_hook(self, hook_type: str, func_name: str, args: str = "", result: str = "", stack: str = ""):
        if self._active_session:
            self._active_session.add_hook(hook_type, func_name, args, result, stack)

    def record_interaction(self, atype: str, **kwargs):
        if self._active_session:
            self._active_session.add_interaction(atype, **kwargs)

    def get_hook_scripts(self) -> str:
        return "\n".join(self._hook_scripts.values())

    def stop_session(self) -> dict:
        if not self._active_session:
            return {"error": "No active session"}
        stats = self._active_session.stats()
        self._active_session.close()
        sid = self._active_session.id
        self._active_session = None
        return {"session_id": sid, **stats}

_capture: CaptureEngine | None = None

def get_capture_engine() -> CaptureEngine:
    global _capture
    if _capture is None: _capture = CaptureEngine()
    return _capture
