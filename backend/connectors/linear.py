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
        """Exchange OAuth code for a Linear access token."""
        data = await self._token_exchange(code)
        self._connected = bool(data and self._access_token)
        return self._connected

    async def disconnect(self) -> None:
        """Clear Linear credentials."""
        await super().disconnect()

    # ------------------------------------------------------------------
    # GraphQL helper
    # ------------------------------------------------------------------

    async def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query against the Linear API."""
        return await self._api_post("/graphql", json_data={
            "query": query,
            "variables": variables or {},
        })

    # ------------------------------------------------------------------
    # Linear API methods
    # ------------------------------------------------------------------

    async def list_issues(self, team_id: str = "", **kwargs) -> dict:
        """List issues, optionally filtered by team."""
        filter_clause = f', filter: {{ team: {{ id: {{ eq: "{team_id}" }} }} }}' if team_id else ""
        query = """query ListIssues {
          issues(first: 50""" + filter_clause + """) {
            nodes {
              id
              title
              identifier
              state { name }
              assignee { name }
              createdAt
            }
          }
        }"""
        return await self._graphql(query)

    async def get_issue(self, issue_id: str) -> dict:
        """Get a single issue by ID."""
        query = """query GetIssue($id: String!) {
          issue(id: $id) {
            id
            title
            description
            identifier
            state { name }
            assignee { name }
            team { name }
            createdAt
            updatedAt
          }
        }"""
        return await self._graphql(query, {"id": issue_id})

    async def create_issue(self, title: str, team_id: str, description: str = "") -> dict:
        """Create a new issue in a team."""
        query = """mutation CreateIssue($title: String!, $teamId: String!, $description: String) {
          issueCreate(input: {
            title: $title
            teamId: $teamId
            description: $description
          }) {
            success
            issue {
              id
              title
              identifier
            }
          }
        }"""
        return await self._graphql(query, {
            "title": title,
            "teamId": team_id,
            "description": description or None,
        })

    async def test_connection(self) -> dict:
        """Test Linear connection with a viewer query."""
        try:
            result = await self._graphql("query { viewer { id name } }")
            viewer = result.get("data", {}).get("viewer", {})
            return {"status": "connected", "viewer": viewer.get("name")}
        except Exception as e:
            return {"status": "error", "message": str(e)}


get_registry().register(LinearConnector())
