"""RE Signature Tracer - trace HMAC/signature/token generation chains in JS."""
from __future__ import annotations
import re, json
from typing import Any

class SignatureTracer:
    """Trace signature generation: find sign functions, track input->output flow."""

    SIGN_FUNC_PATTERNS = {
        "hmac": [r'(?:HmacSHA|hmac|HMAC)\s*\(\s*([^,]+)', r'createHmac\s*\(\s*["\']([^"\']+)["\']'],
        "md5": [r'(?:md5|MD5)\s*\(\s*([^)]+)', r'createHash\s*\(\s*["\']md5["\']'],
        "sha": [r'(?:SHA|sha)\d+\s*\(\s*([^,]+)', r'createHash\s*\(\s*["\']sha(\d+)["\']'],
        "aes": [r'(?:AES|aes)\.(?:encrypt|decrypt)\s*\(\s*([^,]+)'],
        "rsa": [r'(?:RSAKey|JSEncrypt|forge\.pki)', r'new\s+RSAKey', r'\.encrypt\s*\(\s*([^,]+)'],
        "base64": [r'btoa\s*\(\s*([^)]+)', r'Buffer\.from\([^)]+\)\.toString\(\s*["\']base64["\']'],
        "custom_sign": [r'function\s+(?:sign|generateSign|getSign|makeSign|createSign)\w*\s*\(([^)]*)\)\s*\{([^}]{50,500})\}'],
        "token_gen": [r'function\s+(?:getToken|generateToken|makeToken|createToken|refreshToken)\w*\s*\(([^)]*)\)\s*\{([^}]{50,500})\}'],
    }

    KEY_VAR_PATTERNS = [
        r'(?:secret|SECRET|secret_key|SECRET_KEY|private_key|PRIVATE_KEY|app[_-]?secret)\s*[:=]\s*(["\'])([^"\']+)\1',
        r'const\s+(\w*(?:key|KEY|secret|SECRET|sign|SIGN)\w*)\s*=\s*(["\'])([^"\']+)\2',
        r'var\s+(\w*(?:key|KEY|secret|SECRET|sign|SIGN)\w*)\s*=\s*(["\'])([^"\']+)\2',
    ]

    def trace(self, js_code: str) -> dict:
        """Full signature trace: find sign functions, keys, flow."""
        return {
            "sign_functions": self._find_sign_funcs(js_code),
            "key_variables": self._find_keys(js_code),
            "call_chain": self._trace_call_chain(js_code),
            "timestamp_nonce": self._find_timestamps(js_code),
            "summary": self._summarize(js_code),
        }

    def _find_sign_funcs(self, js: str) -> list[dict]:
        found = []
        for category, patterns in self.SIGN_FUNC_PATTERNS.items():
            for pat in patterns:
                for m in re.finditer(pat, js, re.IGNORECASE):
                    ctx_start = max(0, m.start() - 40)
                    ctx_end = min(len(js), m.end() + 80)
                    found.append({
                        "category": category,
                        "match": m.group(0)[:120],
                        "context": js[ctx_start:ctx_end].strip()[:200],
                        "line": js[:m.start()].count('\n') + 1,
                    })
        return found[:40]

    def _find_keys(self, js: str) -> list[dict]:
        found = []
        for pat in self.KEY_VAR_PATTERNS:
            for m in re.finditer(pat, js):
                grps = m.groups()
                var_name = grps[0] if len(grps) >= 2 else "?"
                val = grps[-1] if grps else m.group(0)
                hint = val[:3] + "***" + val[-3:] if len(val) > 8 else val
                found.append({"variable": var_name, "value_hint": hint})
        return found[:20]

    def _trace_call_chain(self, js: str) -> list[dict]:
        """Find the flow: input params -> sign function -> output."""
        chains = []
        # Pattern: params object -> sign(params) -> headers
        sign_lines = re.findall(r'(\w+)\s*=\s*(?:sign|generateSign|getSign|makeSign)\w*\s*\(([^)]+)\)', js)
        for var_name, args in sign_lines[:10]:
            # Find where the result is used
            usage = re.findall(rf'{var_name}\s*[\[.].*?', js)
            chains.append({"result_var": var_name, "arguments": args[:100], "used_in": usage[:5]})
        return chains

    def _find_timestamps(self, js: str) -> list[str]:
        """Find timestamp/nonce generation."""
        patterns = [
            r'Date\.now\(\)', r'new\s+Date\(\)\.getTime\(\)',
            r'Math\.floor\(\s*Date\.now\(\)', r'timestamp\s*[:=]',
            r'\.nonce|nonce\s*[:=]', r'_t\s*[:=]', r'_ts\s*[:=]',
        ]
        found = []
        for pat in patterns:
            matches = re.findall(pat, js, re.IGNORECASE)
            for m in matches:
                if isinstance(m, str):
                    found.append(m)
                else:
                    found.append(str(m))
        return list(set(found))[:15]

    def _summarize(self, js: str) -> str:
        """Generate human-readable summary of signature flow."""
        funcs = self._find_sign_funcs(js)
        keys = self._find_keys(js)
        ts = self._find_timestamps(js)

        lines = []
        categories = set(f["category"] for f in funcs)
        if "hmac" in categories: lines.append("HMAC detected (likely request signing)")
        if "md5" in categories: lines.append("MD5 detected (likely simple hash or legacy)")
        if "sha" in categories: lines.append("SHA detected (hash-based auth)")
        if "aes" in categories: lines.append("AES detected (payload encryption)")
        if "rsa" in categories: lines.append("RSA detected (asymmetric crypto)")
        if "base64" in categories: lines.append("Base64 encoding present")
        if "custom_sign" in categories: lines.append(f"Custom sign function found ({len([f for f in funcs if f['category']=='custom_sign'])} funcs)")
        if "token_gen" in categories: lines.append(f"Token generation logic found ({len([f for f in funcs if f['category']=='token_gen'])} funcs)")
        if keys: lines.append(f"{len(keys)} potential key variables found")
        if ts: lines.append(f"Timestamp/nonce: {', '.join(ts[:5])}")

        return "\n".join(lines) if lines else "No signature patterns detected"


_tracer: SignatureTracer | None = None
def get_signature_tracer() -> SignatureTracer:
    global _tracer
    if _tracer is None: _tracer = SignatureTracer()
    return _tracer
