"""RE Analyzer - auto scene detection, auth chain tracing, crypto fingerprinting."""
from __future__ import annotations
import re, json
from typing import Any

class SceneAnalyzer:
    """Auto-detect site scene type + auth chain + crypto patterns."""

    SCENES = {
        "ai-chat": [(r"chat/completions", 90), (r"text/event-stream", 60)],
        "auth-oauth": [(r"redirect_uri", 80), (r"grant_type", 80), (r"/oauth2?", 70)],
        "auth-login": [(r"/login|/signin|passport", 80), (r"password", 40)],
        "registration": [(r"/register|/signup", 80)],
        "payment": [(r"/pay|/checkout|/order|alipay|stripe", 70)],
        "websocket": [(r"upgrade:\s*websocket", 90)],
        "sse-stream": [(r"text/event-stream", 85)],
        "graphql": [(r"/graphql", 80)],
        "rest-api": [(r"application/json", 30)],
        "file-upload": [(r"multipart/form-data", 70), (r"/upload", 60)],
        "live-stream": [(r"m3u8|\.ts\b|hls", 70)],
        "cdn-proxy": [(r"/image/|/thumbnail/|/resize", 60)],
    }

    AUTH_PATTERNS = {
        "bearer_token": r'Authorization:\s*Bearer\s+(\S+)',
        "basic_auth": r'Authorization:\s*Basic\s+(\S+)',
        "cookie_auth": r'Cookie:\s*([^;]+)',
        "csrf_token": r'[Xx]-[Cc][Ss][Rr][Ff]-[Tt][Oo][Kk][Ee][Nn]:\s*(\S+)',
        "jwt_claim": r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+',
        "api_key_header": r'[Xx]-[Aa][Pp][Ii]-[Kk][Ee][Yy]:\s*(\S+)',
        "refresh_token": r'refresh[_-]?token["\s:=]+([^\s&,"\']+)',
        "session_id": r'session[_-]?id["\s:=]+([^\s&,"\']+)',
    }

    CRYPTO_FINGERPRINTS = {
        "aes-cbc": r'AES[_-]?CBC|aes-[0-9]+-cbc',
        "aes-gcm": r'AES[_-]?GCM|aes-[0-9]+-gcm',
        "rsa-oaep": r'RSA[_-]?OAEP',
        "hmac-sha256": r'HMAC[_-]?SHA256|hmac.*sha256',
        "md5-hash": r'[Mm][Dd]5\s*\(|\.md5\(|md5\s*=\s*',
        "base64-decode": r'atob\(|Buffer\.from\(.*base64',
        "url-encode": r'encodeURIComponent',
        "protobuf": r'\.proto|protobuf|decode\(.*uint8',
    }

    def detect_scene(self, url: str = "", headers_text: str = "", body_text: str = "") -> list[dict]:
        """Detect site type from URL, headers, response body."""
        combined = f"{url}\n{headers_text}\n{body_text}".lower()
        scores = {}
        for scene, patterns in self.SCENES.items():
            for pat, weight in patterns:
                if re.search(pat, combined, re.IGNORECASE):
                    scores[scene] = scores.get(scene, 0) + weight
        return sorted([{"scene": k, "score": min(v, 100)} for k, v in scores.items()], key=lambda x: -x["score"])[:5]

    def trace_auth(self, headers_text: str, body_text: str = "") -> list[dict]:
        """Extract auth tokens and their transformation chain."""
        combined = f"{headers_text}\n{body_text}"
        tokens = []
        for name, pattern in self.AUTH_PATTERNS.items():
            for m in re.finditer(pattern, combined, re.IGNORECASE):
                val = m.group(1) if m.groups() else m.group(0)
                tokens.append({"type": name, "value": val[:100], "full_match": m.group(0)[:200]})
        return tokens[:30]

    def fingerprint_crypto(self, headers_body: str, js_code: str = "") -> list[dict]:
        """Detect crypto algorithms in use."""
        combined = f"{headers_body}\n{js_code}".lower()
        results = []
        for name, pattern in self.CRYPTO_FINGERPRINTS.items():
            matches = re.findall(pattern, combined, re.IGNORECASE)
            if matches:
                results.append({"algorithm": name, "count": len(matches), "samples": list(set(matches))[:5]})
        return results

    def analyze_session(self, session_id: str) -> dict:
        """Full scene analysis on captured session."""
        from .session import get_re_manager
        mgr = get_re_manager()
        sess = mgr.get(session_id)
        if not sess:
            return {"error": "Session not found"}

        requests = sess.get_requests()
        if not requests:
            return {"error": "No captured requests"}

        # Collect all headers & bodies
        all_headers = []
        all_bodies = []
        js_body = ""
        for req in requests:
            all_headers.append(json.dumps(req.get("request_headers", {})))
            all_headers.append(json.dumps(req.get("response_headers", {})))
            body = req.get("response_body", "")
            if req.get("is_js"):
                js_body += body[:50000]
            all_bodies.append(body[:2000])

        headers_text = "\n".join(all_headers)
        body_text = "\n".join(all_bodies)

        return {
            "session_id": session_id,
            "stats": sess.stats(),
            "scenes": self.detect_scene(sess.url, headers_text, body_text),
            "auth_tokens": self.trace_auth(headers_text, body_text),
            "crypto": self.fingerprint_crypto(headers_text, js_body),
            "api_endpoints": sess.get_api_endpoints()[:30],
        }

_analyzer: SceneAnalyzer | None = None
def get_analyzer() -> SceneAnalyzer:
    global _analyzer
    if _analyzer is None: _analyzer = SceneAnalyzer()
    return _analyzer
