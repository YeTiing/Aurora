# Browser Use — CDP浏览器控制插件（对齐Codex browser/chrome插件）
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
    """CDP 浏览器控制 — 通过 Chrome DevTools Protocol"""

    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self._browser = None

    async def ensure_browser(self):
        """启动带CDP的Chrome"""
        port = self.config.remote_debugging_port
        # 检查是否已运行
        try:
            import httpx
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
                if resp.status_code == 200:
                    return True
        except: pass

        # 启动Chrome
        args = [
            self.config.chrome_path,
            f"--remote-debugging-port={port}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-background-networking",
        ]
        if self.config.headless:
            args.append("--headless=new")
        if self.config.user_data_dir:
            args.append(f"--user-data-dir={self.config.user_data_dir}")

        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await asyncio.sleep(2)
        except FileNotFoundError:
            return False
        return True

    async def _cdp(self, target_id: str, method: str, params: dict = None, session_id: str = None):
        """发送CDP命令"""
        import httpx
        ws_key = session_id or target_id
        url = f"http://127.0.0.1:{self.config.remote_debugging_port}/json"
        
        # 获取WebSocket URL
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

        import websockets
        msg = {"id": 1, "method": method}
        if params: msg["params"] = params

        try:
            async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
                await ws.send(json.dumps(msg))
                result = await asyncio.wait_for(ws.recv(), timeout=15)
                return json.loads(result)
        except Exception as e:
            return {"error": str(e)}

    async def navigate(self, url: str, target_id: str = ""):
        """导航到URL"""
        if not target_id:
            # 获取第一个页面
            import httpx
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
                pages = resp.json()
            for p in pages:
                if p.get("type") == "page":
                    target_id = p.get("id")
                    break

        await self._cdp(target_id, "Page.enable")
        return await self._cdp(target_id, "Page.navigate", {"url": url})

    async def screenshot(self, target_id: str = "") -> dict:
        """截取页面截图"""
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()

        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break

        result = await self._cdp(target_id, "Page.captureScreenshot", {"format": "png"})
        if "result" in result and "data" in result["result"]:
            return {
                "data_url": f"data:image/png;base64,{result['result']['data']}",
                "width": 1920, "height": 1080,
            }
        return {"error": str(result)}

    async def click(self, selector: str, target_id: str = ""):
        """点击元素"""
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break

        # 查找元素坐标
        doc = await self._cdp(target_id, "DOM.getDocument", {"depth": -1})
        if "result" not in doc:
            return {"error": str(doc)}

        # 用Runtime.evaluate获取元素位置
        script = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{x: rect.left + rect.width/2, y: rect.top + rect.height/2, width: rect.width, height: rect.height, visible: true}};
        }})()
        """
        result = await self._cdp(target_id, "Runtime.evaluate", {"expression": script, "returnByValue": True})
        if "result" in result and result["result"].get("result", {}).get("value"):
            pos = result["result"]["result"]["value"]
            await self._cdp(target_id, "Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1
            })
            await self._cdp(target_id, "Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1
            })
            return {"clicked": selector, "position": pos}
        return {"error": "Element not found"}

    async def type_text(self, selector: str, text: str, target_id: str = ""):
        """在输入框中输入文本"""
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break

        # Focus element
        script = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (el) {{ el.focus(); el.value = ''; }}
            return !!el;
        }})()
        """
        await self._cdp(target_id, "Runtime.evaluate", {"expression": script, "returnByValue": True})

        # Type text
        for ch in text:
            await self._cdp(target_id, "Input.dispatchKeyEvent", {
                "type": "keyDown", "text": ch, "key": ch,
            })
            await self._cdp(target_id, "Input.dispatchKeyEvent", {
                "type": "keyUp", "text": ch, "key": ch,
            })
        return {"typed": text[:50]}

    async def get_html(self, target_id: str = "") -> str:
        """获取页面HTML"""
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"http://127.0.0.1:{self.config.remote_debugging_port}/json", timeout=5)
            pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                target_id = target_id or p.get("id")
                break

        result = await self._cdp(target_id, "Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True
        })
        if "result" in result:
            return result["result"].get("result", {}).get("value", "")[:50000]
        return ""

    async def list_pages(self) -> list[BrowserPage]:
        """列出所有打开的页面"""
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

    async def close(self):
        if self._browser:
            try: self._browser.kill()
            except: pass

browser_use = BrowserUse()