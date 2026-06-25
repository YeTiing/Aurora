"""Slack API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

SLACK_AUTH_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_API_BASE = "https://slack.com/api"


class SlackConnector(ConnectorBase):
    id = "slack"
    name = "Slack"
    description = "Connect Slack for channel messaging, search, user info, and workspace interaction."
    icon = "slack"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        if config is None:
            config = ConnectorConfig(
                auth_url=SLACK_AUTH_URL,
                token_url=SLACK_TOKEN_URL,
                api_base_url=SLACK_API_BASE,
                scopes=[
                    "channels:read",
                    "channels:history",
                    "chat:write",
                    "search:read",
                    "users:read",
                ],
            )
        super().__init__(config)

    def get_oauth_url(self, state: str = "") -> str:
        return (
            f"{self._config.auth_url}?"
            f"client_id={self._config.client_id}"
            f"&redirect_uri={self._config.redirect_uri}"
            f"&scope={' '.join(self._config.scopes)}"
            f"&state={state}"
        )

    async def handle_callback(self, code: str, state: str = "") -> bool:
        """Exchange OAuth code for a Slack bot token via oauth.v2.access."""
        # Slack oauth.v2.access uses form-encoded POST and returns JSON
        data = await self._token_exchange(code)
        # Slack may return bot token in "access_token" or "authed_user.access_token"
        # The base _token_exchange stores access_token from the top-level key
        if data.get("ok"):
            self._connected = True
        return self._connected

    async def disconnect(self) -> None:
        """Clear Slack credentials."""
        await super().disconnect()

    # ------------------------------------------------------------------
    # Slack API methods
    # ------------------------------------------------------------------

    async def list_channels(self, **kwargs) -> dict:
        """List public channels in the workspace."""
        return await self._api_get("/conversations.list", **kwargs)

    async def post_message(self, channel: str, text: str, **kwargs) -> dict:
        """Post a message to a Slack channel."""
        return await self._api_post(
            "/chat.postMessage",
            json_data={"channel": channel, "text": text, **kwargs},
        )

    async def list_users(self, **kwargs) -> dict:
        """List users in the workspace."""
        return await self._api_get("/users.list", **kwargs)

    async def test_connection(self) -> dict:
        """Test Slack connection with an auth test."""
        try:
            result = await self._api_post("/auth.test")
            return {
                "status": "connected" if result.get("ok") else "error",
                "user": result.get("user"),
                "team": result.get("team"),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


get_registry().register(SlackConnector())
