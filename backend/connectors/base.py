"""Aurora Connector System — base classes for external service integrations."""
from __future__ import annotations
import time
import httpx
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

# ---------------------------------------------------------------------------
# ConnectorConfig
# ---------------------------------------------------------------------------

@dataclass
class ConnectorConfig:
    """OAuth / API configuration for a connector."""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:5173/callback"
    scopes: list[str] = field(default_factory=list)
    auth_url: str = ""
    token_url: str = ""
    api_base_url: str = ""

# ---------------------------------------------------------------------------
# ConnectorBase
# ---------------------------------------------------------------------------

class ConnectorBase(ABC):
    """Abstract base class for all external service connectors."""

    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    icon: ClassVar[str] = "plug"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        self._config = config or ConnectorConfig()
        self._connected = False
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_data: dict = {}

    @property
    def config(self) -> ConnectorConfig:
        return self._config

    @abstractmethod
    def get_oauth_url(self, state: str = "") -> str:
        """Build the OAuth authorization URL for this service."""
        ...

    @abstractmethod
    async def handle_callback(self, code: str, state: str = "") -> bool:
        """Exchange an OAuth authorization code for tokens.  Returns True on success."""
        ...

    def is_connected(self) -> bool:
        """Return whether the connector currently holds valid credentials."""
        return self._connected and self._access_token is not None

    async def disconnect(self) -> None:
        """Revoke tokens and clear local state."""
        self._connected = False
        self._access_token = None
        self._refresh_token = None
        self._token_data = {}

    # ------------------------------------------------------------------
    # OAuth / HTTP helpers
    # ------------------------------------------------------------------

    async def _token_exchange(self, code: str, extra_headers: dict | None = None) -> dict:
        """Exchange an authorization code for tokens via OAuth2 token endpoint.

        Posts form-encoded data to ``self._config.token_url`` and stores the
        resulting ``access_token``, ``refresh_token`` and ``expires_at`` in
        ``self._token_data``.
        """
        async with httpx.AsyncClient() as client:
            headers = {"Accept": "application/json"}
            if extra_headers:
                headers.update(extra_headers)
            resp = await client.post(
                self._config.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._config.redirect_uri,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token")
        if "expires_in" in data:
            self._token_expires_at = time.time() + int(data["expires_in"])
            data["expires_at"] = self._token_expires_at
        self._token_data = data
        return data

    async def refresh_token(self) -> bool:
        """Refresh the access token using the stored refresh token.  Returns True on success."""
        if not self._refresh_token:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._config.token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": self._config.client_id,
                        "client_secret": self._config.client_secret,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return False

        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        if "expires_in" in data:
            self._token_expires_at = time.time() + int(data["expires_in"])
            data["expires_at"] = self._token_expires_at
        self._token_data.update(data)
        return True

    async def _ensure_token(self) -> None:
        """Auto-refresh token if expired (within 30s buffer)."""
        if not self._access_token:
            return
        if self._token_expires_at > 0 and time.time() > self._token_expires_at - 30:
            if self._refresh_token:
                await self.refresh_token()

    async def _api_get(self, path: str, headers: dict | None = None, **kwargs) -> dict:
        """Authenticated GET request to the connector API.  Extra kwargs become query params."""
        await self._ensure_token()
        async with httpx.AsyncClient() as client:
            req_headers = {"Authorization": f"Bearer {self._access_token}"}
            if headers:
                req_headers.update(headers)
            resp = await client.get(
                f"{self._config.api_base_url}{path}",
                headers=req_headers,
                params=kwargs,
            )
            resp.raise_for_status()
            return resp.json()

    async def _api_post(self, path: str, json_data: dict | None = None, headers: dict | None = None, **kwargs) -> dict:
        """Authenticated POST request to the connector API.  Extra kwargs become query params."""
        await self._ensure_token()
        async with httpx.AsyncClient() as client:
            req_headers = {"Authorization": f"Bearer {self._access_token}"}
            if headers:
                req_headers.update(headers)
            resp = await client.post(
                f"{self._config.api_base_url}{path}",
                headers=req_headers,
                json=json_data,
                params=kwargs,
            )
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self) -> dict:
        """Lightweight connectivity check.  Override in subclasses for a real API call."""
        return {
            "status": "connected" if self.is_connected() else "disconnected",
            "has_token": bool(self._access_token),
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "connected": self.is_connected(),
        }


# ---------------------------------------------------------------------------
# ConnectorRegistry
# ---------------------------------------------------------------------------

class ConnectorRegistry:
    """Singleton registry that discovers and manages all ConnectorBase subclasses."""

    _instance: ConnectorRegistry | None = None
    _lock: object = None
    _connectors: dict[str, ConnectorBase]

    def __new__(cls) -> ConnectorRegistry:
        if cls._instance is None:
            import threading
            cls._lock = threading.Lock()
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._connectors = {}
        return cls._instance

    def register(self, connector: ConnectorBase) -> None:
        """Register a connector instance."""
        self._connectors[connector.id] = connector

    def unregister(self, connector_id: str) -> None:
        """Remove a connector from the registry."""
        self._connectors.pop(connector_id, None)

    def get(self, connector_id: str) -> ConnectorBase | None:
        """Get a connector by its id."""
        return self._connectors.get(connector_id)

    def list_all(self) -> list[dict]:
        """Return summary dicts for every registered connector."""
        return [c.to_dict() for c in self._connectors.values()]

    def list_connected(self) -> list[dict]:
        """Return summary dicts for connected connectors only."""
        return [c.to_dict() for c in self._connectors.values() if c.is_connected()]

    @property
    def count(self) -> int:
        return len(self._connectors)


# ---------------------------------------------------------------------------
# module-level convenience
# ---------------------------------------------------------------------------

_registry: ConnectorRegistry | None = None


def get_registry() -> ConnectorRegistry:
    """Get (or create) the global connector registry singleton."""
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
    return _registry


def init_connectors() -> ConnectorRegistry:
    """Discover, instantiate, and register all built-in connectors."""
    registry = get_registry()

    # Import connectors so they self-register via module-level init
    from backend.connectors.figma import FigmaConnector      # noqa: F401
    from backend.connectors.github import GitHubConnector     # noqa: F401
    from backend.connectors.gmail import GmailConnector       # noqa: F401
    from backend.connectors.google_calendar import GoogleCalendarConnector  # noqa: F401
    from backend.connectors.google_drive import GoogleDriveConnector  # noqa: F401
    from backend.connectors.linear import LinearConnector     # noqa: F401
    from backend.connectors.notion import NotionConnector     # noqa: F401
    from backend.connectors.slack import SlackConnector       # noqa: F401

    return registry
