"""Linear API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

LINEAR_AUTH_URL = "https://linear.app/oauth/authorize"
LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"
LINEAR_API_BASE = "https://api.linear.app"


class LinearConnector(ConnectorBase):
    id = "linear"
    name = "Linear"
    description = "Connect Linear for issue tracking, project management, and sprint planning."
    icon = "linear"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        if config is None:
            config = ConnectorConfig(
                auth_url=LINEAR_AUTH_URL,
                token_url=LINEAR_TOKEN_URL,
                api_base_url=LINEAR_API_BASE,
                scopes=["read", "write", "issues:create", "comments:create"],
            )
        super().__init__(config)

    def get_oauth_url(self, state: str = "") -> str:
        return (
            f"{self._config.auth_url}?"
            f"client_id={self._config.client_id}"
            f"&redirect_uri={self._config.redirect_uri}"
            f"&scope={' '.join(self._config.scopes)}"
            f"&state={state}"
            f"&response_type=code"
        )

    async def handle_callback(self, code: str, state: str = "") -> bool:
        # TODO: exchange code for token
        self._connected = True
        return True

    async def disconnect(self) -> None:
        # TODO: revoke Linear personal access token
        self._connected = False
        self._access_token = None


get_registry().register(LinearConnector())
