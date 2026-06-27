# Browser Relay - direct WebSocket bridge to desktop BrowserView (no CDP needed!)
from __future__ import annotations
import asyncio, json, time, uuid, threading

class BrowserRelay:
    """Relay browser commands to Electron desktop via WebSocket."""

    def __init__(self):
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._connected = False
        self._lock = threading.Lock()

    def connected(self) -> bool:
        return self._connected

    def set_ws(self, ws):
        with self._lock:
            self._ws = ws
            self._connected = True

    def clear_ws(self):
        with self._lock:
            self._ws = None
            self._connected = False
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("Desktop disconnected"))
            self._pending.clear()

    async def _send_command(self, method: str, params: dict, timeout: float = 15) -> dict:
        if not self._ws:
            return {"error": "Desktop not connected"}
        cmd_id = uuid.uuid4().hex[:8]
        msg = {"type": "browser_cmd", "id": cmd_id, "method": method, "params": params}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[cmd_id] = fut
        try:
            await self._ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception as e:
            self._pending.pop(cmd_id, None)
            self._connected = False
            return {"error": f"Send failed: {e}"}
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            return {"error": f"Command timed out"}

    def on_result(self, cmd_id: str, result: dict):
        fut = self._pending.pop(cmd_id, None)
        if fut and not fut.done():
            fut.set_result(result)

    async def open(self, url: str) -> dict:
        return await self._send_command("open", {"url": url}, timeout=10)

    async def navigate(self, url: str) -> dict:
        return await self._send_command("navigate", {"url": url})

    async def screenshot(self) -> dict:
        return await self._send_command("screenshot", {})

    async def click(self, selector: str) -> dict:
        return await self._send_command("click", {"selector": selector})

    async def type_text(self, selector: str, text: str) -> dict:
        return await self._send_command("type", {"selector": selector, "text": text})

    async def get_html(self) -> dict:
        return await self._send_command("get_html", {})

    async def evaluate(self, js: str) -> dict:
        return await self._send_command("evaluate", {"js": js})


browser_relay = BrowserRelay()