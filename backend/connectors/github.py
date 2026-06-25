"""GitHub API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"


class GitHubConnector(ConnectorBase):
    id = "github"
    name = "GitHub"
    description = "Connect GitHub for repository access, issues, pull requests, and code search."
    icon = "github"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        if config is None:
            config = ConnectorConfig(
                auth_url=GITHUB_AUTH_URL,
                token_url=GITHUB_TOKEN_URL,
                api_base_url=GITHUB_API_BASE,
                scopes=["repo", "read:org", "workflow"],
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
        # TODO: exchange code for token via self._config.token_url
        self._connected = True
        return True

    async def disconnect(self) -> None:
        # TODO: revoke token or delete OAuth app authorization
        self._connected = False
        self._access_token = None


get_registry().register(GitHubConnector())
