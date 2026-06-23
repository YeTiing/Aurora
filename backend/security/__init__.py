# Aurora Security — 输入消毒 / SSRF防护 / 速率限制 / API认证
from __future__ import annotations
import re, hashlib, hmac, time, secrets, json, ipaddress, socket
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse
import threading


class InputSanitizer:
    SQL_INJECTION_PATTERNS = [
        r"(?i)\b(select|insert|update|delete|drop|alter|create|truncate|union|exec)\b.*\b(from|into|table|database)\b",
        r"(?i)(--|\#|;)\s*$",
        r"(?i)'\s*or\s+('?[^']*'?\s*=\s*'?[^']*'?)",
        r"(?i)\bexec\s*\(\s*@",
        r"(?i)\b(xp_cmdshell|sp_executesql)\b",
    ]

    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript\s*:",
        r"on\w+\s*=\s*[\"'].*?[\"']",
        r"<iframe[^>]*>",
        r"data:text/html",
        r"&#x?[0-9a-f]+;",
    ]

    PATH_TRAVERSAL = [
        r"\.\./",
        r"\.\.\\",
        r"~/.ssh",
        r"/etc/passwd",
        r"C:\\Windows\\System32",
    ]

    COMMAND_INJECTION = [
        r"[;&|`$\(\)]\s*(ls|cat|rm|wget|curl|nc|bash|sh|cmd|powershell)",
        r"\$\(.*\)",
        r"`.*`",
    ]

    MAX_INPUT_LENGTH = 100_000

    @classmethod
    def sanitize_text(cls, text: str, max_length: int = None) -> tuple[str, list[str]]:
        warnings = []
        max_len = max_length or cls.MAX_INPUT_LENGTH

        if len(text) > max_len:
            text = text[:max_len]
            warnings.append(f"Input truncated to {max_len} chars")

        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

        # Check all patterns
        all_checks = [
            (cls.SQL_INJECTION_PATTERNS, "SQL injection"),
            (cls.XSS_PATTERNS, "XSS"),
            (cls.PATH_TRAVERSAL, "Path traversal"),
            (cls.COMMAND_INJECTION, "Command injection"),
        ]

        for patterns, label in all_checks:
            for pattern in patterns:
                if re.search(pattern, cleaned):
                    warnings.append(f"{label} pattern detected")
                    break

        return cleaned, warnings

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        cleaned = re.sub(r'[\\/]', "_", filename)
        cleaned = cleaned.replace("\x00", "")
        cleaned = re.sub(r'[<>:"|?*]', "_", cleaned)
        cleaned = cleaned.strip(". ")
        if len(cleaned) > 255:
            parts = cleaned.rsplit(".", 1)
            cleaned = parts[0][:251] + (f".{parts[1]}" if len(parts) > 1 else "")
        return cleaned or "untitled"

    @classmethod
    def sanitize_code(cls, code: str, language: str = "python") -> tuple[str, list[str]]:
        warnings = []
        if len(code) > 50000:
            code = code[:50000]
            warnings.append("Code truncated to 50000 chars")
        if "\x00" in code:
            code = code.replace("\x00", "")
            warnings.append("Null bytes removed")
        return code, warnings

    @classmethod
    def sanitize_shell_command(cls, command: str) -> tuple[str, list[str]]:
        warnings = []
        dangerous = [
            (r">\s*/dev/", "Blocked: write to device"),
            (r">\s*C:\\", "Blocked: write to C:"),
            (r"rm\s+-rf\s+/", "Blocked: recursive delete root"),
            (r"mkfs\.", "Blocked: filesystem format"),
            (r"dd\s+if=", "Blocked: dd command"),
            (r"chmod\s+777\s+/", "Blocked: world-writable system paths"),
            (r"sudo\s", "Blocked: sudo"),
        ]
        for pattern, warning in dangerous:
            if re.search(pattern, command, re.IGNORECASE):
                warnings.append(warning)
        return command, warnings


class SSRFGuard:
    BLOCKED_HOSTS = [
        "localhost", "127.0.0.1", "0.0.0.0",
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16",
    ]

    BLOCKED_PORTS = {22, 25, 53, 135, 137, 138, 139, 445, 3306, 5432, 27017, 6379, 11211}
    ALLOWED_SCHEMES = {"http", "https"}

    @classmethod
    def validate_url(cls, url: str, allowed_domains: list[str] | None = None) -> tuple[str, str | None]:
        try:
            parsed = urlparse(url)
        except Exception:
            return url, "Invalid URL format"

        if parsed.scheme.lower() not in cls.ALLOWED_SCHEMES:
            return url, f"Blocked scheme: {parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return url, "No hostname in URL"

        if allowed_domains:
            if not any(cls._host_matches(hostname, d) for d in allowed_domains):
                return url, f"Host '{hostname}' not in allowed domains"

        try:
            ip = socket.getaddrinfo(hostname, None)[0][4][0]
        except socket.gaierror:
            return url, f"Cannot resolve hostname: {hostname}"

        if cls._is_private_ip(ip):
            return url, f"Blocked private/internal IP: {ip}"

        port = parsed.port
        if port and port in cls.BLOCKED_PORTS:
            return url, f"Blocked port: {port}"

        return url, None

    @classmethod
    def _host_matches(cls, hostname: str, pattern: str) -> bool:
        if pattern.startswith("*."):
            return hostname.endswith(pattern[1:]) or hostname == pattern[2:]
        return hostname == pattern

    @classmethod
    def _is_private_ip(cls, ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
            return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
        except ValueError:
            return False


class RateLimiter:
    def __init__(self, max_requests: int = 60, window_sec: float = 60.0, block_sec: float = 300.0):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self.block_sec = block_sec
        self._entries: dict[str, RateLimitEntry] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = RateLimitEntry(count=1, reset_at=now + self.window_sec)
                return True, 0
            if entry.blocked_until > now:
                return False, entry.blocked_until - now
            if now >= entry.reset_at:
                entry.count = 1
                entry.reset_at = now + self.window_sec
                return True, 0
            entry.count += 1
            if entry.count > self.max_requests:
                entry.blocked_until = now + self.block_sec
                return False, self.block_sec
            return True, 0

    def reset(self, key: str):
        with self._lock:
            self._entries.pop(key, None)

    def stats(self) -> dict:
        with self._lock:
            blocked = sum(1 for e in self._entries.values() if e.blocked_until > time.time())
            return {"tracked_keys": len(self._entries), "currently_blocked": blocked}


@dataclass
class RateLimitEntry:
    count: int = 0
    reset_at: float = 0.0
    blocked_until: float = 0.0


class APIKeyManager:
    PREFIX = "aur_"
    KEY_LENGTH = 48

    @classmethod
    def generate(cls) -> tuple[str, str]:
        raw = cls.PREFIX + secrets.token_urlsafe(cls.KEY_LENGTH)
        hash_val = hashlib.sha256(raw.encode()).hexdigest()
        return raw, hash_val

    @classmethod
    def verify(cls, key: str, stored_hash: str) -> bool:
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        return hmac.compare_digest(key_hash, stored_hash)

    @classmethod
    def mask(cls, key: str) -> str:
        if len(key) <= 12:
            return "*" * len(key)
        return key[:8] + "*" * (len(key) - 12) + key[-4:]


class SecretsDetector:
    PATTERNS = [
        (r"sk-[A-Za-z0-9\-_]{20,}", "OpenAI API Key"),
        (r"gh[pousr]_[A-Za-z0-9]{36,}", "GitHub Token"),
        (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
        (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "Private Key"),
        (r"(?:api[_-]?key|apikey|secret)\s*[:=]\s*[\"']([A-Za-z0-9_\-]{20,})[\"']", "API Key"),
        (r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", "JWT Token"),
    ]

    @classmethod
    def scan(cls, text: str) -> list[dict]:
        findings = []
        for pattern, label in cls.PATTERNS:
            for match in re.finditer(pattern, text):
                matched = match.group(0)
                if any(ph in matched.lower() for ph in ("your_key", "your_token", "placeholder", "example", "xxx", "test12", "1234567")):
                    continue
                findings.append({
                    "type": label,
                    "line": text[:match.start()].count("\n") + 1,
                    "snippet": matched[:80],
                })
        return findings

    @classmethod
    def redact(cls, text: str) -> str:
        for pattern, _ in cls.PATTERNS:
            text = re.sub(pattern, lambda m: m.group(0)[:4] + "***REDACTED***", text)
        return text


class RequestValidator:
    @staticmethod
    def validate_content_type(headers: dict, expected: str = "application/json") -> tuple[bool, str]:
        ct = headers.get("content-type", "")
        if expected not in ct.lower():
            return False, f"Expected Content-Type: {expected}"
        return True, ""

    @staticmethod
    def validate_json_payload(body: bytes, max_size: int = 10_485_760) -> tuple[Any | None, str]:
        if len(body) > max_size:
            return None, f"Payload too large: {len(body)} bytes (max {max_size})"
        try:
            return json.loads(body), ""
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"

    @staticmethod
    def validate_required_fields(data: dict, required: list[str]) -> tuple[bool, str]:
        missing = [f for f in required if f not in data]
        if missing:
            return False, f"Missing required fields: {', '.join(missing)}"
        return True, ""


class SecurityHeaders:
    @staticmethod
    def get_headers() -> dict[str, str]:
        return {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": SecurityHeaders._csp(),
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }

    @staticmethod
    def _csp() -> str:
        return "; ".join([
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: https:",
            "font-src 'self' data:",
            "connect-src 'self' ws: wss: http://localhost:*",
            "frame-ancestors 'none'",
            "form-action 'self'",
        ])

    @classmethod
    def apply_to_response(cls, response_headers: dict):
        response_headers.update(cls.get_headers())


def secure_filename(filename: str) -> str:
    return InputSanitizer.sanitize_filename(filename)


def sanitize_input(text: str) -> str:
    cleaned, _ = InputSanitizer.sanitize_text(text)
    return cleaned


def validate_url(url: str) -> tuple[str, str | None]:
    return SSRFGuard.validate_url(url)