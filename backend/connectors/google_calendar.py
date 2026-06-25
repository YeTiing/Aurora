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
        """Exchange OAuth code for Google access + refresh tokens."""
        data = await self._token_exchange(code)
        self._connected = True
        return True

    async def disconnect(self) -> None:
        """Clear Calendar credentials."""
        self._refresh_token = None
        await super().disconnect()

    # ------------------------------------------------------------------
    # Google Calendar API methods
    # ------------------------------------------------------------------

    async def list_events(
        self,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        **kwargs,
    ) -> dict:
        """List events on the specified calendar."""
        params = {}
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        params.update(kwargs)
        return await self._api_get(f"/calendars/{calendar_id}/events", **params)

    async def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: dict,
        end: dict,
        **kwargs,
    ) -> dict:
        """Create an event on the specified calendar.

        ``start`` and ``end`` are dicts with ``dateTime`` / ``date`` and ``timeZone`` keys.
        """
        body = {
            "summary": summary,
            "start": start,
            "end": end,
            **kwargs,
        }
        return await self._api_post(
            f"/calendars/{calendar_id}/events",
            json_data=body,
        )

    async def test_connection(self) -> dict:
        """Test Calendar connection by listing a single event."""
        try:
            events = await self.list_events(maxResults=1)
            return {"status": "connected", "event_count": len(events.get("items", []))}
        except Exception as e:
            return {"status": "error", "message": str(e)}


get_registry().register(GoogleCalendarConnector())
