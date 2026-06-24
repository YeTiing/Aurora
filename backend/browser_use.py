# Browser Use - CDP browser control (desktop BrowserView priority, Chrome fallback)
from __future__ import annotations
import subprocess, json, base64, asyncio, time, os, shutil
from dataclasses import dataclass, field
from typing import Any

@dataclass
class BrowserConfig:
    chrome_path: str = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    remote_debugging_port: int = 9222
    user_data_dir: str = ""
    headless: bool = False

@dataclass 
class BrowserPage:
    url: str = ""
    title: str = ""
    target_id: str = ""

class BrowserUse:
    """Browser control - desktop BrowserView priority, Chrome CDP fallback."""

    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self._browser = None
        self._use_desktop: bool | None = None
        self._last_error = ""

    async def _get_desktop_relay(self):
        try:
            from backend.cdp_relay import cdp_relay
            if cdp_relay.connected():
                return cdp_relay
        except ImportError:
            pass
        return None

    async def _cdp_desktop(self, method: str, params: dict | None = None, timeout: float = 8) -> dict:
        relay = await self._get_desktop_relay()
        if not relay:
            return {"error": "Desktop not connected"}
        return await relay.cdp(method, params, timeout)

    async def ensure_browser(self) -> bool:
        """Ensure browser available. Desktop first, then Chrome. Fast-fail."""
        # Try desktop relay (fast)
        relay = await self._get_desktop_relay()
        if relay:
            try:
                r = await asyncio.wait_for(
                    relay.cdp("Browser.open", {"url": "about:blank"}), timeout=5
                )
                if not r.get("error"):
                    self._use_desktop = True
                    return True
            except (asyncio.TimeoutError, Exception):
                pass
            self._use_desktop = None

        # Chrome CDP fallback
        self._use_desktop = False
        port = self.config.remote_debugging_port

        # Check if Chrome CDP already running
        try:
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(3)) as c:
                resp = await c.get(f"http://127.0.0.1:{port}/json/version")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass

        # Find Chrome
        chrome_path = None
        for p in [
            self.config.chrome_path,
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]:
            if os.path.isfile(p):
                chrome_path = p
                break
        if not chrome_path:
            for name in ["chrome", "google-chrome", "chromium", "chromium-browser"]:
                if shutil.which(name):
                    chrome_path = name
                    break

        if not chrome_path:
            self._last_error = "Chrome not found. Start Aurora Desktop for embedded browser, or install Chrome."
            return False

        try:
            args = [
                chrome_path, f"--remote-debugging-port={port}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-extensions",
            ]
            if self.config.headless:
                args.append("--headless=new")
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Wait for CDP to be ready
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
            self._last_error = f"Chrome started but CDP port {port} not responding"
            return False
        except Exception as e:
            self._last_error = f"Failed to start Chrome: {e}"
            return False

    async def _get_error_msg(self) -> str:
        return getattr(self, "_last_error", "Browser unavailable")

    async def navigate(self, url: str, target_id: str = ""):
        if not await self.ensure_browser():
            return {"error": await self._get_error_msg()}
        if self._use_desktop:
            return await self._cdp_desktop("Browser.navigate", {"url": url})
        # Chrome CDP
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json")
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        return await self._cdp_chrome(target_id, "Page.navigate", {"url": url})

    async def screenshot(self, target_id: str = "") -> dict:
        if not await self.ensure_browser():
            return {"error": await self._get_error_msg()}
        if self._use_desktop:
            return await self._cdp_desktop("Browser.screenshot", {})
        # Chrome CDP
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json")
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        result = await self._cdp_chrome(target_id, "Page.captureScreenshot", {"format": "png"})
        if "result" in result and "data" in result["result"]:
            return {"data_url": f"data:image/png;base64,{result['result']['data']}", "width": 1920, "height": 1080}
        return {"error": str(result)}

    async def click(self, selector: str, target_id: str = ""):
        if not await self.ensure_browser():
            return {"error": await self._get_error_msg()}
        if self._use_desktop:
            return await self._cdp_desktop("Runtime.evaluate_and_click", {"selector": selector})
        # Chrome CDP
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json")
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        script = f"""(function(){{var el=document.querySelector('{selector.replace("'", "\\'")}');if(!el)return null;var r=el.getBoundingClientRect();return{{x:r.left+r.width/2,y:r.top+r.height/2,width:r.width,height:r.height,visible:true}};}})()"""
        result = await self._cdp_chrome(target_id, "Runtime.evaluate", {"expression": script, "returnByValue": True})
        if "result" in result and result["result"].get("result", {}).get("value"):
            pos = result["result"]["result"]["value"]
            await self._cdp_chrome(target_id, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            await self._cdp_chrome(target_id, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            return {"clicked": selector, "position": pos}
        return {"error": "Element not found"}

    async def type_text(self, selector: str, text: str, target_id: str = ""):
        if not await self.ensure_browser():
            return {"error": await self._get_error_msg()}
        if self._use_desktop:
            return await self._cdp_desktop("Input.type", {"selector": selector, "text": text})
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json")
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        await self._cdp_chrome(target_id, "Runtime.evaluate", {"expression": f"(function(){{var el=document.querySelector('{selector}');if(el){{el.focus();el.value=''}}return!!el;}})()", "returnByValue": True})
        for ch in text:
            await self._cdp_chrome(target_id, "Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch})
            await self._cdp_chrome(target_id, "Input.dispatchKeyEvent", {"type": "keyUp", "text": ch, "key": ch})
        return {"typed": text[:50]}

    async def get_html(self, target_id: str = "") -> str:
        if not await self.ensure_browser():
            return await self._get_error_msg()
        if self._use_desktop:
            r = await self._cdp_desktop("Runtime.getHTML", {})
            return r.get("html", "")[:50000] if r else ""
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json")
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        result = await self._cdp_chrome(target_id, "Runtime.evaluate", {"expression": "document.documentElement.outerHTML", "returnByValue": True})
        if "result" in result:
            return result["result"].get("result", {}).get("value", "")[:50000]
        return ""

    async def list_pages(self) -> list[BrowserPage]:
        if self._use_desktop:
            r = await self._cdp_desktop("Browser.listPages", {})
            return [BrowserPage(url=r.get("url",""), title="BrowserView")] if r else []
        import httpx
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
                resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json")
                pages = resp.json()
            return [BrowserPage(url=p.get("url",""), title=p.get("title",""), target_id=p.get("id","")) for p in pages if p.get("type") == "page"]
        except Exception:
            return []

    async def _cdp_chrome(self, target_id: str, method: str, params: dict | None = None):
        import httpx, websockets
        url = f"http://127.0.0.1:{self.config.remote_debugging_port}/json"
        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as c:
            resp = await c.get(url)
            pages = resp.json()
        ws_url = None
        for p in pages:
            if p.get("id") == target_id:
                ws_url = p.get("webSocketDebuggerUrl")
                break
        if not ws_url and pages:
            ws_url = pages[0].get("webSocketDebuggerUrl")
        if not ws_url:
            return {"error": "No target found"}
        msg = {"id": 1, "method": method}
        if params:
            msg["params"] = params
        try:
            async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
                await ws.send(json.dumps(msg))
                result = await asyncio.wait_for(ws.recv(), timeout=10)
                return json.loads(result)
        except Exception as e:
            return {"error": str(e)}

    async def close(self):
        if self._browser:
            try:
                self._browser.kill()
            except Exception:
                pass

browser_use = BrowserUse()
