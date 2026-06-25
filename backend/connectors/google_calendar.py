"""Google Calendar API connector."""
from __future__ import annotations
from backend.connectors.base import ConnectorBase, ConnectorConfig, get_registry

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarConnector(ConnectorBase):
    id = "google_calendar"
    name = "Google Calendar"
    description = "Connect Google Calendar for reading, creating, and managing events."
    icon = "calendar"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        if config is None:
            config = ConnectorConfig(
                auth_url=GOOGLE_AUTH_URL,
                token_url=GOOGLE_TOKEN_URL,
                api_base_url=CALENDAR_API_BASE,
                scopes=[
                    "https://www.googleapis.com/auth/calendar.readonly",
                    "https://www.googleapis.com/auth/calendar.events",
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
        # TODO: exchange code for tokens
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False
        self._access_token = None
        self._refresh_token = None


get_registry().register(GoogleCalendarConnector())
