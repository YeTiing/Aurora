"""Notion API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

NOTION_AUTH_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
NOTION_API_BASE = "https://api.notion.com/v1"


class NotionConnector(ConnectorBase):
    id = "notion"
    name = "Notion"
    description = "Connect Notion for database querying, page creation, and workspace access."
    icon = "notion"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        if config is None:
            config = ConnectorConfig(
                auth_url=NOTION_AUTH_URL,
                token_url=NOTION_TOKEN_URL,
                api_base_url=NOTION_API_BASE,
                scopes=[
                    "read:database",
                    "write:database",
                    "read:page",
                    "write:page",
                    "read:user",
                ],
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
        # TODO: exchange code for token (Notion returns access_token directly)
        self._connected = True
        return True

    async def disconnect(self) -> None:
        # TODO: revoke Notion access token
        self._connected = False
        self._access_token = None


get_registry().register(NotionConnector())
