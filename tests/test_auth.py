"""Tests for Aurora OAuth / API Key authentication"""
import pytest
import json
import time
from pathlib import Path
import tempfile
import os

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        old = os.environ.get("AURORA_HOME", "")
        os.environ["AURORA_HOME"] = d
        yield Path(d)
        if old:
            os.environ["AURORA_HOME"] = old
        else:
            os.environ.pop("AURORA_HOME", None)


class TestTokenStore:
    def test_save_and_load(self, temp_dir):
        from backend.auth import TokenStore
        store_path = str(temp_dir / "tokens.enc")
        ts = TokenStore(store_path=store_path)
        ts.save_tokens("openai", "access123", "refresh456", expires_in=3600)
        tokens = ts.load()
        assert "openai" in tokens
        assert tokens["openai"]["access_token"] == "access123"
        assert tokens["openai"]["refresh_token"] == "refresh456"

    def test_clear_tokens(self, temp_dir):
        from backend.auth import TokenStore
        ts = TokenStore(store_path=str(temp_dir / "tokens.enc"))
        ts.save_tokens("openai", "acc", "ref")
        ts.clear_tokens("openai")
        assert ts.load() == {}

    def test_delete(self, temp_dir):
        from backend.auth import TokenStore
        path = str(temp_dir / "tokens.enc")
        ts = TokenStore(store_path=path)
        ts.save({"test": "data"})
        assert Path(path).exists()
        ts.delete()
        assert not Path(path).exists()

    def test_load_empty(self, temp_dir):
        from backend.auth import TokenStore
        ts = TokenStore(store_path=str(temp_dir / "nonexistent.enc"))
        assert ts.load() == {}


class TestAuthManager:
    def test_validate_api_key_valid(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        am.register_api_key("test-key", "sk-test12345")
        valid, name = am.validate_api_key("sk-test12345")
        assert valid is True
        assert name == "test-key"

    def test_validate_api_key_invalid(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        valid, name = am.validate_api_key("bad-key")
        assert valid is False
        assert name == ""

    def test_login_api_key(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        am.register_api_key("my-key", "sk-secret")
        state = am.login_api_key("my-key", "sk-secret")
        assert state.method == "api_key"
        assert state.api_key_name == "my-key"

    def test_login_invalid_key(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        with pytest.raises(Exception):
            am.login_api_key("bad", "wrong")

    def test_get_active_auth_api_key(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        am.register_api_key("k", "sk-abcdefghijklmnop")
        am.login_api_key("k", "sk-abcdefghijklmnop")
        info = am.get_active_auth()
        assert info["method"] == "api_key"
        assert info["authenticated"] is True
        assert "..." in info["masked_key"]

    def test_get_active_auth_none(self, temp_dir):
        from backend.auth import AuthManager, AuthState
        am = AuthManager()
        am._state = AuthState(method="none")
        info = am.get_active_auth()
        assert info["authenticated"] is False
        assert info["method"] == "none"

    def test_logout(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        am.register_api_key("k", "sk-test")
        am.login_api_key("k", "sk-test")
        result = am.logout()
        assert result["logged_out"] is True
        assert am.get_active_auth()["authenticated"] is False

    def test_start_oauth_flow(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        result = am.start_oauth_flow("openai", "http://localhost/callback")
        assert "authorization_url" in result
        assert result["provider"] == "openai"
        assert len(result["state"]) > 0
        assert "code_challenge" in result["authorization_url"].lower() or "openai" in result["authorization_url"]

    def test_pkce_challenge(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        verifier = "test-verifier-string-for-pkce"
        challenge = am.generate_pkce_challenge(verifier)
        assert len(challenge) > 0

    def test_generate_pkce_challenge_known(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        import hashlib, base64
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        challenge = am.generate_pkce_challenge(verifier)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == expected

    def test_set_active_api_key(self, temp_dir):
        from backend.auth import AuthManager
        am = AuthManager()
        state = am.set_active_api_key("sk-direct", "direct-key")
        assert state.method == "api_key"
        assert state.api_key == "sk-direct"


class TestRequireAuth:
    @pytest.mark.asyncio
    async def test_require_auth_unauthenticated(self, temp_dir):
        from backend.auth import auth_manager, require_auth
        from fastapi import HTTPException
        auth_manager.logout()
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_require_auth_with_key(self, temp_dir):
        from backend.auth import auth_manager, require_auth
        auth_manager.set_active_api_key("sk-test", "test")
        state = await require_auth(None)
        assert state.method == "api_key"