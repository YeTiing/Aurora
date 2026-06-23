"""RE API Miner - endpoint discovery from HTML/JS/bundles + secret extraction."""
from __future__ import annotations
import re, json
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

class APIMiner:
    """Extract API endpoints from web source: HTML, JS bundles, network HAR."""

    PATTERNS = {
        "url_strings": [(r'(["\'])(https?://[^"\']+?(?:api|v\d+|graphql|ws|wss)[^"\']*)\1', "api_url"),
                         (r'(["\'])(\/api\/[^"\'\s,;()]{3,})\1', "api_path"),
                         (r'(["\'])(\/v\d+[^"\'\s,;()]{2,})\1', "versioned_path"),
                         (r'(["\'])(\/[a-z]+?\/[a-z]+?(?:\.\w+)?)\1', "generic_path")],
        "fetch_calls": [r'fetch\s*\(\s*(["\'])([^"\']+)\1', r'fetch\s*\(\s*`([^`]+)`'],
        "http_clients": [r'axios\.(?:get|post|put|delete|patch)\s*\(\s*(["\'])([^"\']+)\1',
                         r'\$\.(?:ajax|get|post|getJSON)\s*\(\s*(["\'])([^"\']+)\1',
                         r'requests\.(?:get|post)\s*\(\s*(["\'])([^"\']+)\1'],
        "graphql": [r'(?:graphql|gql|query|mutation)\s*(?:`|["\'])\s*(?:query|mutation)\s+\w+'],
        "websocket": [r'(?:WebSocket|ws)\s*\(\s*(["\'])([^"\']+)\1',
                      r'new\s+WebSocket\s*\(\s*`([^`]+)`'],
        "sse": [r'EventSource\s*\(\s*(["\'])([^"\']+)\1'],
        "hidden_apis": [r'(?:href|src|action|data-url|data-src)\s*=\s*(["\'])([^"\']+)\1'],
    }

    def __init__(self):
        self.results: list[dict] = []

    def mine_text(self, text: str, source: str = "unknown") -> list[dict]:
        """Mine API endpoints from raw text (HTML/JS)."""
        found = []
        for category, patterns in self.PATTERNS.items():
            flat = []
            for p in patterns:
                if isinstance(p, str):
                    flat.append(p)
                elif isinstance(p, (list, tuple)):
                    flat.extend(p)
            for pat in flat:
                for m in re.finditer(pat, text):
                    url = m.group(2) if len(m.groups()) >= 2 else (m.group(1) if m.groups() else m.group(0))
                    url = url.strip()
                    if not self._skip_url(url):
                        found.append({"category": category, "url": url, "source": source})
        return found

    def mine_file(self, filepath: str) -> list[dict]:
        """Mine API endpoints from a file."""
        p = Path(filepath)
        if not p.exists():
            return []
        text = p.read_text(encoding="utf-8", errors="ignore")
        return self.mine_text(text, p.name)

    def mine_from_session(self, session_id: str) -> list[dict]:
        """Mine from captured RE session."""
        from .session import get_re_manager
        mgr = get_re_manager()
        sess = mgr.get(session_id)
        if not sess:
            return []
        results = []
        requests = sess.get_requests()
        for req in requests:
            # From URL paths
            parsed = urlparse(req.get("url", ""))
            path = parsed.path
            if path and not self._skip_url(path):
                results.append({"category": "captured_path", "url": path, "source": f"#{req.get('seq')}", "host": parsed.netloc})
            # From JS response bodies
            if req.get("is_js") or "javascript" in req.get("content_type", ""):
                body = req.get("response_body", "")
                if body:
                    mined = self.mine_text(body[:50000], f"js_response_#{req.get('seq')}")
                    for m in mined:
                        m["host"] = parsed.netloc
                    results.extend(mined)
        return results

    def mine_from_har(self, har_path: str) -> list[dict]:
        """Mine from HAR file."""
        try:
            with open(har_path, encoding="utf-8") as f:
                har = json.load(f)
        except Exception:
            return []
        results = []
        for entry in har.get("log", {}).get("entries", []):
            url = entry.get("request", {}).get("url", "")
            if url and not self._skip_url(url):
                results.append({"category": "har_endpoint", "url": url, "source": har_path})
        return results

    def _skip_url(self, url: str) -> bool:
        """Skip static/uninteresting URLs."""
        skip_domains = ["google", "gtm", "analytics", "fbq", "cdnjs", "jsdelivr", "unpkg", "fonts.googleapis"]
        skip_exts = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".css", ".mp4"]
        lower = url.lower()
        for d in skip_domains:
            if d in lower: return True
        for ext in skip_exts:
            if lower.endswith(ext): return True
        return len(url) < 3


_miner: APIMiner | None = None
def get_api_miner() -> APIMiner:
    global _miner
    if _miner is None: _miner = APIMiner()
    return _miner
