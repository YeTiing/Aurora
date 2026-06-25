"""Aurora Connector System — base classes for external service integrations."""
from __future__ import annotations
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

    @abstractmethod
    async def disconnect(self) -> None:
        """Revoke tokens and clear local state."""
        ...

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
    _connectors: dict[str, ConnectorBase]

    def __new__(cls) -> ConnectorRegistry:
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
