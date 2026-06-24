# Browser CDP Relay — 后端通过桌面端 BrowserView 执行 CDP
from __future__ import annotations
import asyncio, json, time, uuid
from typing import Any

class BrowserCDPRelay:
    """单例：维护与桌面端 WebSocket 的连接，中转 CDP 命令"""

    def __init__(self):
        self._ws = None
        self._pending: dict[int, asyncio.Future] = {}
        self._msg_id = 0

    def connected(self) -> bool:
        return self._ws is not None

    def set_ws(self, ws):
        self._ws = ws

    def clear_ws(self):
        self._ws = None
        # Reject all pending
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("Desktop disconnected"))
        self._pending.clear()

    async def cdp(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        """Send CDP command to desktop BrowserView and wait for result."""
        if not self._ws:
            return {"error": "Desktop not connected — falling back to Chrome CDP"}

        self._msg_id += 1
        mid = self._msg_id
        msg = {
            "type": "browser:cdp",
            "id": mid,
            "method": method,
            "params": params or {},
        }

        try:
            await self._ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception as e:
            self.clear_ws()
            return {"error": f"Send failed: {e}"}

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            return {"error": f"CDP command timed out after {timeout}s"}
        except Exception as e:
            self._pending.pop(mid, None)
            return {"error": str(e)}

    def on_result(self, msg_id: int, result: dict):
        fut = self._pending.pop(msg_id, None)
        if fut and not fut.done():
            fut.set_result(result)

    async def ensure_open(self, url: str = "about:blank") -> dict:
        """Ensure BrowserView is open and navigated to URL."""
        # First try to open browser via CDP — will use ipcMain handler
        r = await self.cdp("Browser.open", {"url": url})
        if r.get("error"):
            return r
        # Wait for navigation
        await asyncio.sleep(1.5)
        return await self.cdp("Browser.getState", {})

    async def navigate(self, url: str) -> dict:
        return await self.cdp("Browser.navigate", {"url": url})

    async def screenshot(self) -> dict:
        return await self.cdp("Browser.screenshot", {})

    async def click(self, selector: str) -> dict:
        return await self.cdp("Runtime.evaluate_and_click", {"selector": selector})

    async def type_text(self, selector: str, text: str) -> dict:
        return await self.cdp("Input.type", {"selector": selector, "text": text})

    async def get_html(self) -> dict:
        return await self.cdp("Runtime.getHTML", {})

    async def evaluate(self, expression: str) -> dict:
        return await self.cdp("Runtime.evaluate", {"expression": expression})


cdp_relay = BrowserCDPRelay()
