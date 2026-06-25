"""Figma API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

FIGMA_AUTH_URL = "https://www.figma.com/oauth"
FIGMA_TOKEN_URL = "https://www.figma.com/api/oauth/token"
FIGMA_API_BASE = "https://api.figma.com/v1"


class FigmaConnector(ConnectorBase):
    id = "figma"
    name = "Figma"
    description = "Connect Figma for design file access, comments, and component inspection."
    icon = "figma"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        if config is None:
            config = ConnectorConfig(
                auth_url=FIGMA_AUTH_URL,
                token_url=FIGMA_TOKEN_URL,
                api_base_url=FIGMA_API_BASE,
                scopes=["file_read", "file_comments:read"],
            )
        super().__init__(config)

    def get_oauth_url(self, state: str = "") -> str:
        return (
            f"{self._config.auth_url}?"
            f"client_id={self._config.client_id}"
            f"&redirect_uri={self._config.redirect_uri}"
            f"&scope={'+'.join(self._config.scopes)}"
            f"&state={state}"
            f"&response_type=code"
        )

    async def handle_callback(self, code: str, state: str = "") -> bool:
        # TODO: implement token exchange via self._config.token_url
        self._connected = True
        return True

    async def disconnect(self) -> None:
        # TODO: revoke token via Figma API
        self._connected = False
        self._access_token = None


# Auto-register on import
get_registry().register(FigmaConnector())
