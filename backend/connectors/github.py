"""GitHub API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"


class GitHubConnector(ConnectorBase):
    id = "github"
    name = "GitHub"
    description = "Connect GitHub for repo access, issues, PRs, and code search."
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
        data = await self._token_exchange(code)
        if data and "access_token" in data:
            self._access_token = data["access_token"]
            self._connected = True
        return self._connected

    async def test_connection(self) -> dict:
        return await self._api_get("/user")

    async def get_user(self) -> dict:
        return await self._api_get("/user")

    async def list_repos(self, per_page: int = 30) -> list[dict]:
        return await self._api_get(f"/user/repos?per_page={per_page}&sort=updated")

    async def search_code(self, query: str, per_page: int = 10) -> dict:
        return await self._api_get(f"/search/code?q={query}&per_page={per_page}")

    async def get_file(self, owner: str, repo: str, path: str, ref: str = "main") -> dict:
        return await self._api_get(f"/repos/{owner}/{repo}/contents/{path}?ref={ref}")

    async def list_issues(self, owner: str, repo: str, state: str = "open") -> list[dict]:
        return await self._api_get(f"/repos/{owner}/{repo}/issues?state={state}")

    async def disconnect(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._connected = False
        self._token_data = {}


get_registry().register(GitHubConnector())
