# Aurora OAuth 2.0 & API Key authentication
# Supports API Key login and OAuth 2.0 PKCE flow
from __future__ import annotations
import os, json, time, hashlib, base64, secrets, uuid
from pathlib import Path
from typing import Any, Optional, Callable
from dataclasses import dataclass, field
from fastapi import HTTPException, Request
from cryptography.fernet import Fernet

DEFAULT_DATA_DIR = Path(os.environ.get("AURORA_HOME", Path.home() / ".aurora"))
OAUTH_PROVIDERS = {
    "openai": {
        "authorize_url": "https://auth.openai.com/authorize",
        "token_url": "https://auth.openai.com/oauth/token",
        "scopes": "openid profile email offline_access model.read",
    },
}


@dataclass
class AuthState:
    method: str = "none"  # "api_key", "oauth", "none"
    api_key: str = ""
    api_key_name: str = ""
    access_token: str = ""
    refresh_token: str = ""
    token_expires_at: float = 0.0
    provider: str = ""
    user_info: dict = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        if self.method == "api_key":
            return bool(self.api_key)
        if self.method == "oauth":
            return bool(self.access_token)
        if self.method == "none":
            return True  # no auth configured
        return False


class TokenStore:
    def __init__(self, store_path: str = ""):
        if not store_path:
            store_path = str(DEFAULT_DATA_DIR / "tokens.enc")
        self._path = Path(store_path)
        self._key_path = self._path.parent / ".fernet_key"
        self._fernet = self._init_fernet()

    def _init_fernet(self) -> Fernet:
        if self._key_path.exists():
            key = self._key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            self._key_path.write_bytes(key)
            import os
            try:
                if os.name == "nt":
                    import subprocess
                    subprocess.run(["icacls", str(self._key_path), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME','')}:(R)"], capture_output=True)
            except Exception:
                import logging
                logging.getLogger("aurora").debug("fernet key load failed", exc_info=True)
        return Fernet(key)

    def save(self, data: dict) -> None:
        plain = json.dumps(data).encode("utf-8")
        encrypted = self._fernet.encrypt(plain)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(encrypted)

    def load(self) -> dict:
        if not self._path.exists():
            return {}
        encrypted = self._path.read_bytes()
        plain = self._fernet.decrypt(encrypted)
        return json.loads(plain.decode("utf-8"))

    def delete(self) -> None:
        if self._path.exists():
            self._path.unlink()

    def save_tokens(self, provider: str, access_token: str, refresh_token: str, expires_in: int = 3600) -> None:
        tokens = self.load()
        tokens[provider] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + expires_in,
        }
        self.save(tokens)

    def get_tokens(self, provider: str) -> dict:
        return self.load().get(provider, {})

    def clear_tokens(self, provider: str | None = None) -> None:
        if provider is None:
            self.delete()
        else:
            tokens = self.load()
            tokens.pop(provider, None)
            self.save(tokens)


class AuthManager:
    def __init__(self, token_store: TokenStore | None = None):
        self._state = AuthState()
        self._store = token_store or TokenStore()
        self._api_keys: dict[str, str] = {}  # name -> key
        self._api_key_hashes: dict[str, str] = {}  # sha256(key) -> name
        self._oauth_flows: dict[str, dict] = {}  # state -> flow info
        self._load_saved_state()

    def _load_saved_state(self) -> None:
        try:
            saved = self._store.load()
            if "auth_state" in saved:
                s = saved["auth_state"]
                self._state = AuthState(
                    method=s.get("method", "none"),
                    api_key=s.get("api_key", ""),
                    api_key_name=s.get("api_key_name", ""),
                    provider=s.get("provider", ""),
                )
        except Exception:
            import logging
            logging.getLogger("aurora").debug("auth persist failed", exc_info=True)

    def _save_state(self) -> None:
        try:
            tokens = self._store.load()
            tokens["auth_state"] = {
                "method": self._state.method,
                "api_key": self._state.api_key,
                "api_key_name": self._state.api_key_name,
                "provider": self._state.provider,
            }
            self._store.save(tokens)
        except Exception:
            import logging
            logging.getLogger("aurora").debug("auth persist failed", exc_info=True)

    def register_api_key(self, name: str, key: str) -> None:
        self._api_keys[name] = key
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        self._api_key_hashes[key_hash] = name

    def validate_api_key(self, key: str) -> tuple[bool, str]:
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        if key_hash in self._api_key_hashes:
            return True, self._api_key_hashes[key_hash]
        for name, stored_key in self._api_keys.items():
            if stored_key == key:
                return True, name
        return False, ""

    def login_api_key(self, name: str, key: str) -> AuthState:
        valid, key_name = self.validate_api_key(key)
        if not valid:
            if name and name in self._api_keys:
                pass
            else:
                raise HTTPException(401, "Invalid API key")
        self._state = AuthState(
            method="api_key",
            api_key=key,
            api_key_name=name or key_name,
        )
        self._save_state()
        return self._state

    def generate_pkce_challenge(self, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def start_oauth_flow(self, provider: str = "openai", redirect_uri: str = "") -> dict:
        prov = OAUTH_PROVIDERS.get(provider)
        if not prov:
            raise HTTPException(400, f"Unknown OAuth provider: {provider}")
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = self.generate_pkce_challenge(code_verifier)
        self._oauth_flows[state] = {
            "provider": provider,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "created_at": time.time(),
        }
        params = {
            "response_type": "code",
            "client_id": "aurora-agent",
            "redirect_uri": redirect_uri,
            "scope": prov["scopes"],
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        auth_url = f"{prov['authorize_url']}?{query}"
        return {
            "authorization_url": auth_url,
            "state": state,
            "provider": provider,
        }

    async def complete_oauth_flow(self, code: str, state: str, http_client=None) -> AuthState:
        flow = self._oauth_flows.pop(state, None)
        if not flow:
            raise HTTPException(400, "Invalid or expired OAuth state")
        if time.time() - flow["created_at"] > 600:
            raise HTTPException(400, "OAuth flow expired (10 min)")
        provider = flow["provider"]
        prov = OAUTH_PROVIDERS.get(provider, {})
        token_url = prov.get("token_url", "")
        if not token_url:
            raise HTTPException(400, f"No token URL for provider: {provider}")
        if http_client is None:
            import httpx
            http_client = httpx.AsyncClient(timeout=30.0)
        try:
            payload = {
                "grant_type": "authorization_code",
                "client_id": "aurora-agent",
                "code": code,
                "redirect_uri": flow["redirect_uri"],
                "code_verifier": flow["code_verifier"],
            }
            resp = await http_client.post(token_url, data=payload)
            if resp.status_code != 200:
                raise HTTPException(401, f"Token exchange failed: {resp.text[:200]}")
            data = resp.json()
            access_token = data.get("access_token", "")
            refresh_token = data.get("refresh_token", "")
            expires_in = data.get("expires_in", 3600)
            self._store.save_tokens(provider, access_token, refresh_token, expires_in)
            self._state = AuthState(
                method="oauth",
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=time.time() + expires_in,
                provider=provider,
            )
            self._save_state()
            return self._state
        finally:
            pass

    async def refresh_token_if_needed(self, http_client=None) -> AuthState:
        if self._state.method != "oauth":
            return self._state
        if time.time() < self._state.token_expires_at - 60:
            return self._state
        if not self._state.refresh_token:
            raise HTTPException(401, "No refresh token available")
        prov = OAUTH_PROVIDERS.get(self._state.provider, {})
        token_url = prov.get("token_url", "")
        if not token_url:
            raise HTTPException(401, "No token URL for refresh")
        if http_client is None:
            import httpx
            http_client = httpx.AsyncClient(timeout=30.0)
        try:
            payload = {
                "grant_type": "refresh_token",
                "client_id": "aurora-agent",
                "refresh_token": self._state.refresh_token,
            }
            resp = await http_client.post(token_url, data=payload)
            if resp.status_code != 200:
                self._state = AuthState(method="none")
                self._save_state()
                raise HTTPException(401, "Token refresh failed")
            data = resp.json()
            self._state.access_token = data.get("access_token", "")
            self._state.refresh_token = data.get("refresh_token", self._state.refresh_token)
            expires_in = data.get("expires_in", 3600)
            self._state.token_expires_at = time.time() + expires_in
            self._store.save_tokens(self._state.provider, self._state.access_token, self._state.refresh_token, expires_in)
            self._save_state()
            return self._state
        finally:
            pass

    def get_active_auth(self) -> dict:
        masked = ""
        if self._state.method == "api_key" and self._state.api_key:
            masked = self._state.api_key[:6] + "..." + self._state.api_key[-4:] if len(self._state.api_key) > 10 else "***"
        elif self._state.method == "oauth" and self._state.access_token:
            masked = self._state.access_token[:8] + "..."
        return {
            "method": self._state.method,
            "provider": self._state.provider,
            "masked_key": masked,
            "api_key_name": self._state.api_key_name,
            "authenticated": self._state.is_authenticated,
            "token_expires_at": self._state.token_expires_at if self._state.method == "oauth" else None,
        }

    def logout(self) -> dict:
        old_method = self._state.method
        old_provider = self._state.provider
        self._state = AuthState(method="none")
        self._save_state()
        if old_method == "oauth":
            self._store.clear_tokens(old_provider)
        return {"logged_out": True, "previous_method": old_method}

    def set_active_api_key(self, api_key: str, name: str = "default") -> AuthState:
        self._state = AuthState(method="api_key", api_key=api_key, api_key_name=name)
        self._save_state()
        return self._state


auth_manager = AuthManager()


async def require_auth(request: Request) -> AuthState:
    state = auth_manager.get_active_auth()
    if not state["authenticated"]:
        raise HTTPException(401, "Authentication required. Use /auth/login or /auth/oauth/login.")
    if auth_manager._state.method == "oauth":
        try:
            await auth_manager.refresh_token_if_needed()
        except Exception:
            raise HTTPException(401, "Token expired. Please re-authenticate.")
    return auth_manager._state