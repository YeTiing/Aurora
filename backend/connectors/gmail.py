"""Gmail API connector (Google OAuth)."""
from __future__ import annotations
import base64
from email.mime.text import MIMEText
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailConnector(ConnectorBase):
    id = "gmail"
    name = "Gmail"
    description = "Connect Gmail for reading, sending, and searching email."
    icon = "mail"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        if config is None:
            config = ConnectorConfig(
                auth_url=GOOGLE_AUTH_URL,
                token_url=GOOGLE_TOKEN_URL,
                api_base_url=GMAIL_API_BASE,
                scopes=[
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.send",
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
        """Clear Gmail credentials.  Optionally revoke the Google token."""
        self._refresh_token = None
        await super().disconnect()

    # ------------------------------------------------------------------
    # Gmail API methods
    # ------------------------------------------------------------------

    async def list_messages(self, query: str = "", max_results: int = 10, **kwargs) -> dict:
        """List messages in the user's mailbox matching an optional query."""
        params = {"maxResults": max_results}
        if query:
            params["q"] = query
        params.update(kwargs)
        return await self._api_get("/users/me/messages", **params)

    async def send_message(self, to: str, subject: str, body: str) -> dict:
        """Send an email from the authenticated user."""
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        return await self._api_post(
            "/users/me/messages/send",
            json_data={"raw": raw},
        )

    async def get_profile(self) -> dict:
        """Get the Gmail user's profile."""
        return await self._api_get("/users/me/profile")

    async def test_connection(self) -> dict:
        """Test Gmail connection by fetching the user profile."""
        try:
            profile = await self.get_profile()
            return {"status": "connected", "email": profile.get("emailAddress")}
        except Exception as e:
            return {"status": "error", "message": str(e)}


get_registry().register(GmailConnector())
