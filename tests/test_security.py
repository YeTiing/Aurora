import sys, pytest, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from security import (
    InputSanitizer, SSRFGuard, RateLimiter, APIKeyManager,
    SecretsDetector, RequestValidator, SecurityHeaders,
    secure_filename, sanitize_input, validate_url,
)


class TestInputSanitizer:
    def test_sanitize_text_clean(self):
        text, warnings = InputSanitizer.sanitize_text("hello world")
        assert text == "hello world"
        assert len(warnings) == 0

    def test_sanitize_text_null_bytes(self):
        text, warnings = InputSanitizer.sanitize_text("hello\x00world")
        assert "\x00" not in text

    def test_sanitize_text_sql_warning(self):
        text, warnings = InputSanitizer.sanitize_text("SELECT * FROM users; DROP TABLE users--")
        assert len(warnings) > 0

    def test_sanitize_text_xss(self):
        text, warnings = InputSanitizer.sanitize_text("<script>alert(1)</script>")
        assert len(warnings) > 0

    def test_sanitize_text_truncation(self):
        long_text = "x" * 200000
        text, warnings = InputSanitizer.sanitize_text(long_text, max_length=10000)
        assert len(text) == 10000
        assert len(warnings) > 0

    def test_sanitize_filename(self):
        assert InputSanitizer.sanitize_filename("hello/world") == "hello_world"
        assert InputSanitizer.sanitize_filename("test<>:.txt") == "test___.txt"
        assert InputSanitizer.sanitize_filename("") == "untitled"

    def test_sanitize_shell_cmd(self):
        cmd, warnings = InputSanitizer.sanitize_shell_command("rm -rf /etc/passwd")
        assert len(warnings) > 0

    def test_secure_filename(self):
        assert secure_filename("test.txt") == "test.txt"

    def test_sanitize_input(self):
        assert sanitize_input("normal text") == "normal text"


class TestSSRFGuard:
    def test_validate_ok_url(self):
        url, err = SSRFGuard.validate_url("https://api.openai.com/v1/chat")
        assert err is None

    def test_validate_localhost(self):
        url, err = SSRFGuard.validate_url("http://localhost:8080/api")
        assert err is not None

    def test_validate_bad_scheme(self):
        url, err = SSRFGuard.validate_url("file:///etc/passwd")
        assert err is not None

    def test_validate_url_func(self):
        url, err = validate_url("https://google.com")
        assert err is None


class TestRateLimiter:
    def test_allow_first(self):
        rl = RateLimiter(max_requests=5, window_sec=60)
        allowed, _ = rl.check("user1")
        assert allowed

    def test_block_exceed(self):
        rl = RateLimiter(max_requests=3, window_sec=60, block_sec=0.1)
        for _ in range(3):
            rl.check("user2")
        allowed, retry = rl.check("user2")
        assert not allowed

    def test_different_keys(self):
        rl = RateLimiter(max_requests=2, window_sec=60)
        assert rl.check("user_a")[0]
        assert rl.check("user_a")[0]
        assert rl.check("user_b")[0]

    def test_reset(self):
        rl = RateLimiter(max_requests=2, window_sec=60)
        rl.check("user_c")
        rl.check("user_c")
        rl.reset("user_c")
        assert rl.check("user_c")[0]


class TestAPIKeyManager:
    def test_generate_and_verify(self):
        raw, hashed = APIKeyManager.generate()
        assert raw.startswith("aur_")
        assert APIKeyManager.verify(raw, hashed)
        assert not APIKeyManager.verify("wrong_key", hashed)

    def test_mask(self):
        masked = APIKeyManager.mask("aur_abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJKLM")
        assert "*" in masked


class TestSecretsDetector:
    def test_detect_openai_key(self):
        findings = SecretsDetector.scan("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456")
        assert any("OpenAI" in f["type"] for f in findings)

    def test_detect_github_token(self):
        findings = SecretsDetector.scan("token = ghp_AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGhIjKlMnOp12")
        assert any("GitHub" in f["type"] for f in findings)

    def test_no_false_positive(self):
        findings = SecretsDetector.scan('api_key = "your_key_here"')
        assert len(findings) == 0

    def test_redact(self):
        redacted = SecretsDetector.redact("sk-thisisatestkeythatislongenough12345")
        assert "***REDACTED***" in redacted


class TestRequestValidator:
    def test_content_type_ok(self):
        ok, msg = RequestValidator.validate_content_type({"content-type": "application/json"})
        assert ok

    def test_content_type_bad(self):
        ok, msg = RequestValidator.validate_content_type({"content-type": "text/html"})
        assert not ok

    def test_required_fields(self):
        ok, msg = RequestValidator.validate_required_fields({"a": 1}, ["a", "b"])
        assert not ok
        assert "b" in msg

        ok, msg = RequestValidator.validate_required_fields({"a": 1, "b": 2}, ["a", "b"])
        assert ok


class TestSecurityHeaders:
    def test_get_headers(self):
        headers = SecurityHeaders.get_headers()
        assert "X-Content-Type-Options" in headers
        assert "Content-Security-Policy" in headers
        assert headers["X-Frame-Options"] == "DENY"