"""Google Drive API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


class GoogleDriveConnector(ConnectorBase):
    id = "google_drive"
    name = "Google Drive"
    description = "Connect Google Drive for file listing, reading, creating, and searching."
    icon = "hard-drive"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        if config is None:
            config = ConnectorConfig(
                auth_url=GOOGLE_AUTH_URL,
                token_url=GOOGLE_TOKEN_URL,
                api_base_url=DRIVE_API_BASE,
                scopes=[
                    "https://www.googleapis.com/auth/drive.readonly",
                    "https://www.googleapis.com/auth/drive.file",
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
            f"&access_type=offline"
            f"&prompt=consent"
        )

    async def handle_callback(self, code: str, state: str = "") -> bool:
        """Exchange OAuth code for Google access + refresh tokens."""
        data = await self._token_exchange(code)
        self._connected = True
        return True

    async def disconnect(self) -> None:
        """Clear Drive credentials."""
        self._refresh_token = None
        await super().disconnect()

    # ------------------------------------------------------------------
    # Google Drive API methods
    # ------------------------------------------------------------------

    async def list_files(self, query: str = "", page_size: int = 20, **kwargs) -> dict:
        """List files in Google Drive, optionally filtered by query."""
        params = {"pageSize": page_size}
        if query:
            params["q"] = query
        params.update(kwargs)
        return await self._api_get("/files", **params)

    async def get_file(self, file_id: str, **kwargs) -> dict:
        """Get metadata for a specific file by ID."""
        return await self._api_get(f"/files/{file_id}", **kwargs)

    async def create_file(self, name: str, mime_type: str = "text/plain", content: str = "") -> dict:
        """Create a new file in Google Drive with the given content."""
        import io
        import json

        metadata = {"name": name, "mimeType": mime_type}
        boundary = "aurora_drive_boundary"
        body_lines = [
            f"--{boundary}",
            "Content-Type: application/json; charset=UTF-8",
            "",
            json.dumps(metadata),
            f"--{boundary}",
            "Content-Type: text/plain",
            "",
            content,
            f"--{boundary}--",
        ]
        body = "\r\n".join(body_lines)

        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{DRIVE_API_BASE}/files?uploadType=multipart",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                content=body,
            )
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self) -> dict:
        """Test Drive connection by listing one file."""
        try:
            files = await self.list_files(page_size=1)
            return {"status": "connected", "file_count": len(files.get("files", []))}
        except Exception as e:
            return {"status": "error", "message": str(e)}


get_registry().register(GoogleDriveConnector())
