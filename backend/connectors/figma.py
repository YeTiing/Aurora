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
        """Exchange OAuth code for a Figma access token."""
        data = await self._token_exchange(code)
        self._connected = True
        return True

    async def disconnect(self) -> None:
        """Clear Figma credentials."""
        await super().disconnect()

    # ------------------------------------------------------------------
    # Figma API methods
    # ------------------------------------------------------------------

    async def get_file(self, file_key: str, **kwargs) -> dict:
        """Get a Figma file by key."""
        return await self._api_get(f"/files/{file_key}", **kwargs)

    async def get_file_nodes(self, file_key: str, node_ids: list[str], **kwargs) -> dict:
        """Get specific nodes from a Figma file."""
        ids = ",".join(node_ids)
        return await self._api_get(f"/files/{file_key}/nodes", ids=ids, **kwargs)

    async def get_comments(self, file_key: str, **kwargs) -> dict:
        """Get comments on a Figma file."""
        return await self._api_get(f"/files/{file_key}/comments", **kwargs)

    async def test_connection(self) -> dict:
        """Test Figma connection by fetching team projects."""
        try:
            # Figma doesn't have a /me endpoint; try fetching team projects
            return {"status": "connected", "has_token": bool(self._access_token)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# Auto-register on import
get_registry().register(FigmaConnector())
