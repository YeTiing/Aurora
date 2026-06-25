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
        # TODO: exchange code for bot token via oauth.v2.access
        self._connected = True
        return True

    async def disconnect(self) -> None:
        # TODO: call auth.revoke to revoke token
        self._connected = False
        self._access_token = None


get_registry().register(SlackConnector())
