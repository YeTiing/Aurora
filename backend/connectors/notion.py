"""Notion API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

NOTION_AUTH_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


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
        """Exchange OAuth code for a Notion access token."""
        # Notion OAuth returns access_token directly (no refresh token).
        data = await self._token_exchange(code)
        self._connected = True
        return True

    async def disconnect(self) -> None:
        """Clear Notion credentials."""
        await super().disconnect()

    # ------------------------------------------------------------------
    # Notion API methods
    # ------------------------------------------------------------------

    def _notion_headers(self) -> dict:
        """Return headers required by the Notion API."""
        return {
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def search(self, query: str = "", **kwargs) -> dict:
        """Search all pages and databases in the workspace."""
        body = {"query": query, **kwargs} if query else kwargs
        return await self._api_post("/search", json_data=body, headers=self._notion_headers())

    async def get_page(self, page_id: str) -> dict:
        """Get a Notion page by ID."""
        return await self._api_get(f"/pages/{page_id}", headers=self._notion_headers())

    async def create_page(self, parent_id: str, title: str, content: str = "") -> dict:
        """Create a new page inside a parent page or database."""
        body = {
            "parent": {"page_id": parent_id},
            "properties": {
                "title": {
                    "title": [{"text": {"content": title}}],
                },
            },
        }
        if content:
            body["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": content}}],
                    },
                },
            ]
        return await self._api_post("/pages", json_data=body, headers=self._notion_headers())

    async def test_connection(self) -> dict:
        """Test Notion connection with a search."""
        try:
            result = await self.search(page_size=1)
            return {"status": "connected", "result_count": len(result.get("results", []))}
        except Exception as e:
            return {"status": "error", "message": str(e)}


get_registry().register(NotionConnector())
