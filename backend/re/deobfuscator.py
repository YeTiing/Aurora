"""RE Deobfuscator - JS deobfuscation, crypto call tracing, syntax extraction."""
from __future__ import annotations
import re, json
from pathlib import Path
from typing import Any


class Deobfuscator:
    """JS deobfuscation engine. Regex-based + optional AST (via esprima/babel)."""

    # Crypto library patterns
    CRYPTO_PATTERNS = {
        "CryptoJS": [r"CryptoJS\.(\w+)", r"CryptoJS\.(AES|DES|TripleDES|RC4|Rabbit)\.(\w+)"],
        "WebCrypto": [r"crypto\.subtle\.(\w+)", r"window\.crypto\.subtle\.(\w+)"],
        "JSEncrypt": [r"new\s+JSEncrypt", r"JSEncrypt\.prototype\.(\w+)"],
        "forge": [r"forge\.(md|pki|cipher|hmac|util)\.(\w+)"],
        "sm-crypto": [r"sm2\.(doEncrypt|doDecrypt|generateKeyPairHex)", r"sm3\(|sm4\.(encrypt|decrypt)"],
        "Buffer": [r"Buffer\.from\(([^)]+)\)", r"\.toString\(['\"](base64|hex)['\"]"],
        "btoa/atob": [r"btoa\(([^)]+)\)", r"atob\(([^)]+)\)"],
        "custom_encrypt": [r"function\s+(\w*(?:encrypt|decrypt|sign|hash)\w*)\s*\(", r"var\s+(\w*(?:encrypt|decrypt|sign|hash)\w*)\s*=\s*function"],
    }

    # Obfuscation patterns
    OBFUSCATION_PATTERNS = {
        "hex_strings": r'\\x[0-9a-fA-F]{2}',
        "unicode_escape": r'\\u[0-9a-fA-F]{4}',
        "string_array": r'var\s+\w+\s*=\s*\[([^\]]{100,})\]',
        "eval_unpack": r'eval\s*\(\s*function\s*\(',
        "packer": r'eval\(function\(p,a,c,k,e,[dr]\)',
        "rotated_strings": r'String\.fromCharCode\([^)]{20,}\)',
        "jjencode": r'[\[\]\(\)!\+]{\s*\$',
        "aaencode": r'[\u4e00-\u9fff]{10,}',
    }

    def __init__(self):
        self.last_result: dict = {}

    def analyze(self, js_code: str, filename: str = "unknown.js") -> dict:
        """Full JS analysis: crypto calls, obfuscation detection, API patterns."""
        result = {
            "file": filename,
            "size": len(js_code),
            "crypto_calls": self.find_crypto(js_code),
            "obfuscation": self.detect_obfuscation(js_code),
            "api_endpoints": self.extract_api_patterns(js_code),
            "secrets": self.extract_secrets(js_code),
            "imports_exports": self.extract_modules(js_code),
            "eval_blocks": self.find_evals(js_code),
        }
        self.last_result = result
        return result

    def find_crypto(self, js: str) -> list[dict]:
        """Find crypto library usage."""
        found = []
        for lib, patterns in self.CRYPTO_PATTERNS.items():
            for pat in patterns:
                for m in re.finditer(pat, js):
                    ctx = self._context(js, m.start(), 120)
                    found.append({"library": lib, "match": m.group(0), "context": ctx.strip()})
        return found[:50]

    def detect_obfuscation(self, js: str) -> list[dict]:
        """Detect obfuscation techniques."""
        results = []
        for technique, pattern in self.OBFUSCATION_PATTERNS.items():
            matches = re.findall(pattern, js)
            if matches:
                results.append({"technique": technique, "count": len(matches), "samples": matches[:3]})
        return results

    def extract_api_patterns(self, js: str) -> list[dict]:
        """Extract API endpoint patterns from JS."""
        patterns = [
            (r'(["\'])(\/api\/[^"\'\\]{3,})\1', "api_path"),
            (r'(["\'])(\/v\d+\/[^"\'\\]{3,})\1', "versioned_api"),
            (r'fetch\s*\(\s*(["\'])([^"\']+)\1', "fetch_call"),
            (r'axios\.(?:get|post|put|delete|patch)\s*\(\s*(["\'])([^"\']+)\1', "axios_call"),
            (r'\.request\s*\(\s*\{[^}]*url\s*:\s*(["\'])([^"\']+)\1', "request_url"),
            (r'WebSocket\s*\(\s*(["\'])([^"\']+)\1', "websocket"),
            (r'EventSource\s*\(\s*(["\'])([^"\']+)\1', "sse"),
            (r'(?:base_url|api_url|API_URL|BASE_URL|endpoint)\s*[:=]\s*(["\'])([^"\']+)\1', "base_url"),
            (r'(?:domain|host)\s*[:=]\s*(["\'])([^"\']+)\1', "host_config"),
        ]
        results = []
        for pat, ptype in patterns:
            for m in re.finditer(pat, js):
                url = m.group(2) if len(m.groups()) >= 2 else m.group(1)
                results.append({"type": ptype, "url": url, "position": m.start()})
        return list({r["url"]: r for r in results}.values())[:50]

    def extract_secrets(self, js: str) -> list[dict]:
        """Extract potential secrets/keys/tokens."""
        patterns = [
            (r'(?:api[_-]?key|apikey|API_KEY|SECRET_KEY)\s*[:=]\s*(["\'])([^"\']{8,})\1', "api_key"),
            (r'(?:token|access_token|auth_token|jwt)\s*[:=]\s*(["\'])([^"\']{10,})\1', "token"),
            (r'(?:secret|client_secret)\s*[:=]\s*(["\'])([^"\']{10,})\1', "secret"),
            (r'(?:password|passwd)\s*[:=]\s*(["\'])([^"\']{3,})\1', "password"),
            (r'Authorization\s*:\s*(["\'])([^"\']+)\1', "auth_header"),
            (r'(?:sk-[a-zA-Z0-9]{20,})', "openai_key"),
            (r'(?:ghp_[a-zA-Z0-9]{36})', "github_token"),
        ]
        results = []
        for pat, stype in patterns:
            for m in re.finditer(pat, js):
                val = m.group(2) if len(m.groups()) >= 2 else m.group(0)
                results.append({"type": stype, "value_hint": val[:3] + "***" + val[-3:] if len(val) > 6 else val})
        return results[:20]

    def extract_modules(self, js: str) -> dict:
        """Extract import/export statements."""
        imports = re.findall(r'(?:import\s+.*?from\s+["\']([^"\']+)["\']|require\s*\(\s*["\']([^"\']+)["\']\))', js)
        exports = re.findall(r'export\s+(?:default\s+)?(?:class|function|const|let|var)\s+(\w+)', js)
        return {"imports": [i[0] or i[1] for i in imports][:30], "exports": exports[:30]}

    def find_evals(self, js: str) -> list[str]:
        """Find eval/new Function calls."""
        evals = re.findall(r'(eval|new\s+Function)\s*\(', js)
        return evals

    def _context(self, text: str, pos: int, width: int) -> str:
        start = max(0, pos - width // 2)
        return text[start:pos + width // 2]

    def beautify(self, js: str) -> str:
        """Try to beautify minified JS."""
        try:
            import jsbeautifier
            return jsbeautifier.beautify(js)
        except ImportError:
            return self._basic_format(js)

    def _basic_format(self, js: str) -> str:
        """Basic formatting: add newlines after statements."""
        js = re.sub(r';(?!\n)', ';\n', js)
        js = re.sub(r'\{(?!\n)', '{\n', js)
        js = re.sub(r'\}(?![\n,;\)\]])', '}\n', js)
        return js

    def to_report(self, result: dict | None = None) -> str:
        """Generate human-readable report."""
        r = result or self.last_result
        lines = [f"=== Deobfuscation Report: {r.get('file', '?')} ===", f"Size: {r.get('size', 0):,} chars", ""]
        if r.get("obfuscation"):
            lines.append("## Obfuscation Detected")
            for o in r["obfuscation"]:
                lines.append(f"  - {o['technique']}: {o['count']} occurrences")
            lines.append("")
        if r.get("crypto_calls"):
            lines.append(f"## Crypto Calls ({len(r['crypto_calls'])} found)")
            for c in r["crypto_calls"][:20]:
                lines.append(f"  - [{c['library']}] {c['match']}")
            lines.append("")
        if r.get("api_endpoints"):
            lines.append(f"## API Endpoints ({len(r['api_endpoints'])} found)")
            for a in r["api_endpoints"][:15]:
                lines.append(f"  - [{a['type']}] {a['url']}")
            lines.append("")
        if r.get("secrets"):
            lines.append(f"## Potential Secrets ({len(r['secrets'])} found)")
            for s in r["secrets"]:
                lines.append(f"  - [{s['type']}] {s['value_hint']}")
        return "\n".join(lines)

_deob: Deobfuscator | None = None
def get_deobfuscator() -> Deobfuscator:
    global _deob
    if _deob is None: _deob = Deobfuscator()
    return _deob
