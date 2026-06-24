# Browser Use — CDP浏览器控制（优先桌面BrowserView，回退Chrome CDP）
from __future__ import annotations
import subprocess, json, base64, asyncio, time, os
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
    """浏览器控制 — 优先使用桌面端 BrowserView，回退到 Chrome CDP"""

    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self._browser = None
        self._use_desktop: bool | None = None  # None = auto-detect

    async def _get_desktop_relay(self):
        """Get CDP relay if desktop is connected."""
        try:
            from backend.cdp_relay import cdp_relay
            if cdp_relay.connected():
                return cdp_relay
        except ImportError:
            pass
        return None

    async def _cdp_desktop(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        """Execute CDP via desktop BrowserView."""
        relay = await self._get_desktop_relay()
        if not relay:
            return {"error": "Desktop not connected"}
        return await relay.cdp(method, params, timeout)

    async def ensure_browser(self):
        """确保浏览器可用（桌面端优先）"""
        relay = await self._get_desktop_relay()
        if relay:
            self._use_desktop = True
            # Ensure BrowserView is open
            r = await relay.cdp("Browser.open", {"url": "about:blank"})
            if not r.get("error"):
                self._use_desktop = True
                return True
            # Desktop BrowserView failed, fall back
            self._use_desktop = False

        # Fallback: try existing Chrome with CDP first
        self._use_desktop = False
        port = self.config.remote_debugging_port
        try:
            import httpx
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
                if resp.status_code == 200:
                    return True
        except: pass

        # Try to launch Chrome
        chrome_paths = [
            self.config.chrome_path,
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "chrome", "google-chrome", "chromium", "chromium-browser",
        ]
        launched = False
        for chrome in chrome_paths:
            try:
                args = [chrome, f"--remote-debugging-port={port}",
                        "--no-first-run", "--no-default-browser-check",
                        "--disable-extensions", "--disable-background-networking"]
                if self.config.headless:
                    args.append("--headless=new")
                if self.config.user_data_dir:
                    args.append(f"--user-data-dir={self.config.user_data_dir}")
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                await asyncio.sleep(2)
                launched = True
                break
            except FileNotFoundError:
                continue
        if not launched:
            self._last_error = "Desktop not connected and Chrome not found. Start Aurora desktop or install Chrome."
            return False
        
        # Verify it started
        try:
            import httpx
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"http://127.0.0.1:{port}/json/version", timeout=3)
                if resp.status_code == 200:
                    return True
        except: pass
        self._last_error = f"Chrome started but CDP on port {port} not responding. Check if port is blocked."
        return False

    async def navigate(self, url: str, target_id: str = ""):
        """导航到URL"""
        await self.ensure_browser()
        if self._use_desktop:
            return await self._cdp_desktop("Browser.navigate", {"url": url})
        # Chrome CDP fallback
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        return await self._cdp_chrome(target_id, "Page.navigate", {"url": url})

    async def screenshot(self, target_id: str = "") -> dict:
        """截取页面截图"""
        await self.ensure_browser()
        if self._use_desktop:
            return await self._cdp_desktop("Browser.screenshot", {})
        # Chrome CDP fallback
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        result = await self._cdp_chrome(target_id, "Page.captureScreenshot", {"format": "png"})
        if "result" in result and "data" in result["result"]:
            return {
                "data_url": f"data:image/png;base64,{result['result']['data']}",
                "width": 1920, "height": 1080,
            }
        return {"error": str(result)}

    async def click(self, selector: str, target_id: str = ""):
        """点击元素"""
        await self.ensure_browser()
        if self._use_desktop:
            return await self._cdp_desktop("Runtime.evaluate_and_click", {"selector": selector})
        # Chrome CDP fallback
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break

        script = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{x: rect.left + rect.width/2, y: rect.top + rect.height/2, width: rect.width, height: rect.height, visible: true}};
        }})()
        """
        result = await self._cdp_chrome(target_id, "Runtime.evaluate", {"expression": script, "returnByValue": True})
        if "result" in result and result["result"].get("result", {}).get("value"):
            pos = result["result"]["result"]["value"]
            await self._cdp_chrome(target_id, "Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1
            })
            await self._cdp_chrome(target_id, "Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1
            })
            return {"clicked": selector, "position": pos}
        return {"error": "Element not found"}

    async def type_text(self, selector: str, text: str, target_id: str = ""):
        """在输入框中输入文本"""
        await self.ensure_browser()
        if self._use_desktop:
            return await self._cdp_desktop("Input.type", {"selector": selector, "text": text})
        # Chrome CDP fallback
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break

        script = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (el) {{ el.focus(); el.value = ''; }}
            return !!el;
        }})()
        """
        await self._cdp_chrome(target_id, "Runtime.evaluate", {"expression": script, "returnByValue": True})
        for ch in text:
            await self._cdp_chrome(target_id, "Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch})
            await self._cdp_chrome(target_id, "Input.dispatchKeyEvent", {"type": "keyUp", "text": ch, "key": ch})
        return {"typed": text[:50]}

    async def get_html(self, target_id: str = "") -> str:
        """获取页面HTML"""
        await self.ensure_browser()
        if self._use_desktop:
            r = await self._cdp_desktop("Runtime.getHTML", {})
            return r.get("html", "")[:50000] if r else ""
        # Chrome CDP fallback
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break
        result = await self._cdp_chrome(target_id, "Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML", "returnByValue": True
        })
        if "result" in result:
            return result["result"].get("result", {}).get("value", "")[:50000]
        return ""

    async def list_pages(self) -> list[BrowserPage]:
        """列出所有打开的页面"""
        await self.ensure_browser()
        if self._use_desktop:
            r = await self._cdp_desktop("Browser.listPages", {})
            return [BrowserPage(url=r.get("url",""), title="BrowserView")] if r else []
        import httpx
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
                pages = resp.json()
            return [
                BrowserPage(url=p.get("url",""), title=p.get("title",""), target_id=p.get("id",""))
                for p in pages if p.get("type") == "page"
            ]
        except:
            return []

    async def _cdp_chrome(self, target_id: str, method: str, params: dict = None):
        """Chrome CDP (fallback)"""
        import httpx, websockets
        url = f"http://127.0.0.1:{self.config.remote_debugging_port}/json"
        async with httpx.AsyncClient() as c:
            resp = await c.get(url, timeout=5)
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
        if params: msg["params"] = params
        try:
            async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
                await ws.send(json.dumps(msg))
                result = await asyncio.wait_for(ws.recv(), timeout=15)
                return json.loads(result)
        except Exception as e:
            return {"error": str(e)}

    async def close(self):
        if self._browser:
            try: self._browser.kill()
            except: pass

browser_use = BrowserUse()
