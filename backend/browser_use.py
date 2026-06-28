"""Browser use - Electron BrowserView priority, Chrome fallback."""
from __future__ import annotations
import subprocess, json, base64, asyncio, time, os, shutil
from dataclasses import dataclass
from typing import Any

@dataclass
class BrowserPage:
    url: str = ""
    title: str = ""

class BrowserUse:
    """Browser via Electron desktop (priority) or Chrome CDP (fallback)."""

    def __init__(self):
        self._last_error = ""
        self._use_desktop = False
        self._chrome_port = 9223

    async def _get_relay(self):
        try:
            from backend.browser_relay import browser_relay
            if browser_relay.connected():
                self._use_desktop = True
                return browser_relay
        except ImportError:
            pass
        return None

    async def ensure_browser(self) -> bool:
        # 1. Try desktop BrowserView
        relay = await self._get_relay()
        if relay:
            try:
                r = await asyncio.wait_for(relay._send_command("get_state", {}), timeout=3)
                if not r.get("error"):
                    return True
            except Exception:
                pass

        # 2. Try Chrome CDP
        self._use_desktop = False
        port = self._chrome_port

        # Check if Chrome already running with CDP
        try:
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(3)) as c:
                resp = await c.get(f"http://127.0.0.1:{port}/json/version")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass

        # Launch Chrome
        chrome = None
        for p in [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]:
            if os.path.isfile(p):
                chrome = p
                break
        if not chrome:
            for name in ["chrome", "google-chrome", "chromium"]:
                if shutil.which(name):
                    chrome = name
                    break
        if not chrome:
            self._last_error = "No browser available. Install Chrome or start Aurora Desktop."
            return False

        try:
            subprocess.Popen(
                [chrome, f"--remote-debugging-port={port}",
                 "--no-first-run", "--no-default-browser-check",
                 "--disable-extensions", "--headless=new"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(10):
                await asyncio.sleep(0.5)
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=httpx.Timeout(1)) as c:
                        resp = await c.get(f"http://127.0.0.1:{port}/json/version")
                        if resp.status_code == 200:
                            return True
                except Exception:
                    continue
            self._last_error = f"Chrome started but CDP not responding on port {port}"
            return False
        except Exception as e:
            self._last_error = f"Failed to start Chrome: {e}"
            return False

    async def navigate(self, url: str, target_id: str = ""):
        relay = await self._get_relay()
        if relay:
            return await relay.open(url)
        # Chrome CDP
        if not await self.ensure_browser():
            return {"error": self._last_error}
        import httpx, websockets
        port = self._chrome_port
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{port}/json")
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        ws_url = None
        for p in pages:
            if p.get("id") == target_id:
                ws_url = p.get("webSocketDebuggerUrl")
                break
        if not ws_url and pages:
            ws_url = pages[0].get("webSocketDebuggerUrl")
        if not ws_url:
            # Create new page
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
                r = await c.put(f"http://127.0.0.1:{port}/json/new?{url}")
                ws_url = r.json().get("webSocketDebuggerUrl", "")
        if not ws_url:
            return {"error": "No browser target found"}
        msg = {"id": 1, "method": "Page.enable"}
        try:
            async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
                await ws.send(json.dumps(msg))
                await asyncio.wait_for(ws.recv(), timeout=10)
                msg2 = {"id": 2, "method": "Page.navigate", "params": {"url": url}}
                await ws.send(json.dumps(msg2))
                result = await asyncio.wait_for(ws.recv(), timeout=15)
                return json.loads(result)
        except Exception as e:
            return {"error": str(e)}

    async def screenshot(self, target_id: str = "") -> dict:
        relay = await self._get_relay()
        if relay:
            return await relay.screenshot()
        if not await self.ensure_browser():
            return {"error": self._last_error}
        import httpx, websockets
        port = self._chrome_port
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{port}/json")
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        ws_url = next((p["webSocketDebuggerUrl"] for p in pages if p.get("id") == target_id), pages[0].get("webSocketDebuggerUrl", ""))
        if not ws_url:
            return {"error": "No target"}
        try:
            async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
                result = await asyncio.wait_for(ws.recv(), timeout=15)
                r = json.loads(result)
                if "result" in r and "data" in r["result"]:
                    return {"data_url": f"data:image/png;base64,{r['result']['data']}", "width": 1920, "height": 1080}
                return {"error": str(r)}
        except Exception as e:
            return {"error": str(e)}

    async def click(self, selector: str, target_id: str = ""):
        relay = await self._get_relay()
        if relay:
            return await relay.click(selector)
        if not await self.ensure_browser():
            return {"error": self._last_error}
        import httpx, websockets
        port = self._chrome_port
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{port}/json")
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        ws_url = next((p["webSocketDebuggerUrl"] for p in pages if p.get("id") == target_id), pages[0].get("webSocketDebuggerUrl", ""))
        if not ws_url:
            return {"error": "No target"}
        try:
            async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
                import json as _json
                safe_sel = _json.dumps(selector)
                js = f"(function(){{var el=document.querySelector({safe_sel});if(!el)return null;var r=el.getBoundingClientRect();return{{x:r.left+r.width/2,y:r.top+r.height/2}};}})()"
                await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": js, "returnByValue": True}}))
                pos_result = await asyncio.wait_for(ws.recv(), timeout=10)
                pos = json.loads(pos_result).get("result", {}).get("result", {}).get("value")
                if not pos:
                    return {"error": f"Element not found: {selector}"}
                await ws.send(json.dumps({"id": 2, "method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}}))
                await ws.send(json.dumps({"id": 3, "method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}}))
                for _ in range(2):
                    await asyncio.wait_for(ws.recv(), timeout=5)
                return {"clicked": selector, "position": pos}
        except Exception as e:
            return {"error": str(e)}

    async def type_text(self, selector: str, text: str, target_id: str = ""):
        relay = await self._get_relay()
        if relay:
            return await relay.type_text(selector, text)
        return {"error": "Type only available via desktop BrowserView, not Chrome CDP"}

    async def get_html(self, target_id: str = "") -> str:
        relay = await self._get_relay()
        if relay:
            return await relay.get_html()
        if not await self.ensure_browser():
            return self._last_error
        import httpx, websockets
        port = self._chrome_port
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{port}/json")
            pages = resp.json()
        ws_url = pages[0].get("webSocketDebuggerUrl", "") if pages else ""
        if not ws_url:
            return "No browser target"
        try:
            async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "document.documentElement.outerHTML", "returnByValue": True}}))
                result = await asyncio.wait_for(ws.recv(), timeout=15)
                return json.loads(result).get("result", {}).get("result", {}).get("value", "")[:50000]
        except Exception as e:
            return str(e)

    async def list_pages(self) -> list[BrowserPage]:
        relay = await self._get_relay()
        if relay:
            r = await relay._send_command("get_state", {})
            return [BrowserPage(url=r.get("url",""), title="BrowserView")]
        import httpx
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3)) as c:
                resp = await c.get(f"http://127.0.0.1:{self._chrome_port}/json")
                pages = resp.json()
            return [BrowserPage(url=p.get("url",""), title=p.get("title","")) for p in pages if p.get("type") == "page"]
        except Exception:
            return []

    async def close(self):
        """Close browser — relay first, then kill self-launched Chrome."""
        # 1. Close Electron BrowserView via relay
        try:
            from backend.browser_relay import browser_relay
            if browser_relay.connected():
                await browser_relay._send_command("close", {}, timeout=3)
                browser_relay.clear_ws()
        except Exception:
            pass

        # 2. Kill Chrome if we launched it (self-managed CDP)
        if not self._use_desktop:
            port = self._chrome_port
            try:
                import httpx
                async with httpx.AsyncClient(timeout=httpx.Timeout(3)) as c:
                    pages = await c.get(f"http://127.0.0.1:{port}/json")
                    for p in pages.json():
                        wid = p.get("id", "")
                        if wid:
                            await c.get(f"http://127.0.0.1:{port}/json/close/{wid}")
            except Exception:
                pass

        self._last_error = ""
        self._use_desktop = False


browser_use = BrowserUse()
