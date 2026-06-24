"""Browser use - Electron BrowserView (no external Chrome needed)."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass

@dataclass 
class BrowserPage:
    url: str = ""
    title: str = ""

class BrowserUse:
    """Browser control via desktop Electron BrowserView. No Chrome needed."""

    def __init__(self):
        self._last_error = ""

    async def _get_relay(self):
        try:
            from backend.browser_relay import browser_relay
            if browser_relay.connected():
                return browser_relay
        except ImportError:
            pass
        return None

    async def ensure_browser(self) -> bool:
        relay = await self._get_relay()
        if relay:
            return True
        self._last_error = (
            "Browser unavailable. Aurora Desktop is not connected. "
            "Start the Aurora Desktop app and try again."
        )
        return False

    async def navigate(self, url: str, target_id: str = ""):
        relay = await self._get_relay()
        if not relay:
            return {"error": self._last_error or "Desktop not connected"}
        return await relay.open(url)

    async def screenshot(self, target_id: str = "") -> dict:
        relay = await self._get_relay()
        if not relay:
            return {"error": self._last_error or "Desktop not connected"}
        return await relay.screenshot()

    async def click(self, selector: str, target_id: str = ""):
        relay = await self._get_relay()
        if not relay:
            return {"error": self._last_error or "Desktop not connected"}
        return await relay.click(selector)

    async def type_text(self, selector: str, text: str, target_id: str = ""):
        relay = await self._get_relay()
        if not relay:
            return {"error": self._last_error or "Desktop not connected"}
        return await relay.type_text(selector, text)

    async def get_html(self, target_id: str = "") -> str:
        relay = await self._get_relay()
        if not relay:
            return self._last_error or "Desktop not connected"
        return await relay.get_html()

    async def list_pages(self) -> list[BrowserPage]:
        relay = await self._get_relay()
        if relay:
            r = await relay._send_command("get_state", {})
            return [BrowserPage(url=r.get("url",""), title="BrowserView")]
        return []

    async def close(self):
        pass


browser_use = BrowserUse()
