# Aurora Computer Use -- Windows desktop automation (Codex-aligned)
# Supports: Direct API + JSON-line subprocess IPC (like codex-computer-use.exe)
from __future__ import annotations
import asyncio, base64, json, time, os, ctypes, subprocess, threading, queue
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
import mss, pyautogui, uiautomation as uia

pyautogui.FAILSAFE = False
user32 = ctypes.windll.user32


@dataclass
class WindowInfo:
    app: str = ""
    id: int = 0
    title: str = ""
    rect: tuple = (0, 0, 0, 0)
    pid: int = 0
    is_active: bool = False

    def to_dict(self) -> dict:
        return {
            "app": self.app, "id": self.id, "title": self.title,
            "bounds": {"originX": self.rect[0], "originY": self.rect[1],
                        "width": self.rect[2] - self.rect[0],
                        "height": self.rect[3] - self.rect[1]},
            "pid": self.pid, "isActive": self.is_active,
        }


@dataclass
class ScreenshotInfo:
    id: str = ""
    data_url: str = ""
    width: int = 0
    height: int = 0
    z_index: int = 0
    origin_x: int = 0
    origin_y: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "url": self.data_url,
            "width": self.width, "height": self.height,
            "zIndex": self.z_index,
            "originX": self.origin_x, "originY": self.origin_y,
        }


@dataclass
class WindowState:
    window: WindowInfo = field(default_factory=WindowInfo)
    screenshots: list = field(default_factory=list)
    accessibility: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "window": self.window.to_dict(),
            "screenshots": [s.to_dict() for s in self.screenshots],
            "accessibility": self.accessibility,
        }


# Security blacklist -- Codex-aligned
FORBIDDEN_APPS = [
    "cmd.exe", "powershell.exe", "wt.exe", "WindowsTerminal.exe",
    "1Password.exe", "Bitwarden.exe", "KeePass.exe", "LastPass.exe",
    "taskmgr.exe", "regedit.exe", "msconfig.exe", "gpedit.msc",
    "Codex.exe", "Aurora.exe", "electron.exe",
]

FORBIDDEN_KEY_COMBOS = [
    "win+l", "win+r", "win+d", "win+m", "ctrl+alt+del",
    "alt+f4", "ctrl+shift+esc",
]

ESCAPE_INTERRUPT_FILE_PREFIX = ".aurora_cu_interrupt"


class ComputerUse:
    """Computer Use engine -- direct API mode (pyautogui + mss + UIA)"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ---- Screenshot ----
    def screenshot(self, monitor=0):
        with mss.mss() as sct:
            mons = sct.monitors
            mon = mons[monitor] if monitor < len(mons) else mons[0]
            img = sct.grab(mon)
            png = mss.tools.to_png(img.rgb, img.size)
            b64 = base64.b64encode(png).decode()
            return ScreenshotInfo(
                id=f"ss-{int(time.time()*1000)}",
                data_url=f"data:image/png;base64,{b64}",
                width=img.width, height=img.height,
                origin_x=mon["left"], origin_y=mon["top"],
            )

    def _check_escape(self):
        import pathlib
        marker = pathlib.Path(f"{ESCAPE_INTERRUPT_FILE_PREFIX}_{os.getpid()}")
        if marker.exists():
            marker.unlink()
            raise InterruptedError("Computer Use was stopped by Escape key")

    def screenshot_to_file(self, path=""):
        with mss.mss() as sct:
            img = sct.grab(sct.monitors[0])
            png = mss.tools.to_png(img.rgb, img.size)
            dest = path or f"screenshot_{int(time.time())}.png"
            with open(dest, "wb") as f:
                f.write(png)
            return os.path.abspath(dest)

    # ---- Windows ----
    def list_windows(self):
        wins = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _enum(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                t = buf.value or ""
                if t.strip():
                    r = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(r))
                    fore = user32.GetForegroundWindow()
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    proc_name = self._proc_name(pid.value)
                    if any(f in proc_name.lower() for f in FORBIDDEN_APPS):
                        return True
                    wins.append(WindowInfo(
                        app=proc_name, id=hwnd, title=t, pid=pid.value,
                        rect=(r.left, r.top, r.right, r.bottom),
                        is_active=(hwnd == fore),
                    ))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        wins.sort(key=lambda w: w.is_active, reverse=True)
        return wins[:30]

    def list_apps(self):
        """List installed apps (simplified -- returns running windows by process)"""
        wins = self.list_windows()
        apps = {}
        for w in wins:
            key = w.app.lower().replace(".exe", "")
            if key not in apps:
                apps[key] = {
                    "id": w.app, "displayName": w.title.split(" - ")[-1] if " - " in w.title else w.app,
                    "windows": [], "isRunning": True,
                }
            apps[key]["windows"].append(w.to_dict())
        return list(apps.values())

    def get_window(self, title_contains="", hwnd=0):
        if hwnd and user32.IsWindow(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return WindowInfo(app=self._proc_name(pid.value), id=hwnd, title=buf.value or "",
                rect=(r.left, r.top, r.right, r.bottom), pid=pid.value)
        if title_contains:
            lower = title_contains.lower()
            for w in self.list_windows():
                if lower in w.title.lower():
                    return w
        fg = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(fg, buf, 256)
        r = wintypes.RECT()
        user32.GetWindowRect(fg, ctypes.byref(r))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        return WindowInfo(app=self._proc_name(pid.value), id=fg, title=buf.value or "",
            rect=(r.left, r.top, r.right, r.bottom), pid=pid.value, is_active=True)

    def _proc_name(self, pid):
        try:
            import psutil
            proc = psutil.Process(pid)
            name = proc.name() or ""
            # Check if forbidden
            name_lower = name.lower()
            for fb in FORBIDDEN_APPS:
                if fb.lower() in name_lower:
                    return name
            return name
        except:
            return f"pid:{pid}"

    # ---- Accessibility tree (UIA) ----
    def get_accessibility_tree(self, hwnd=0, max_depth=4):
        try:
            root = uia.ControlFromHandle(hwnd) if hwnd and user32.IsWindow(hwnd) else uia.GetForegroundControl()
            elems = []
            self._walk_tree(root, elems, max_depth, 0)
            tree_text = self._format_tree(elems)
            focused = ""
            selected = ""
            try:
                if hasattr(root, 'GetFocusedControl'):
                    fc = root.GetFocusedControl()
                    if fc:
                        focused = f"#{fc.ControlTypeName}: {fc.Name}"[:120]
            except: pass
            try:
                if hasattr(root, 'GetSelectedItems'):
                    si = root.GetSelectedItems()
                    if si:
                        selected = [f"{s.ControlTypeName}: {s.Name}" for s in si[:5]]
                        selected = "; ".join(selected)[:200]
            except: pass
            return {
                "tree": tree_text,
                "focused_element": focused,
                "selected_text": selected,
                "element_count": len(elems),
            }
        except Exception as e:
            return {"tree": "", "error": str(e)[:200], "element_count": 0}

    def _walk_tree(self, control, elems, maxd, d):
        if d >= maxd: return
        try:
            for child in control.GetChildren():
                try:
                    ct = child.ControlTypeName or "Unknown"
                    nm = child.Name or ""
                    val = ""
                    try:
                        if hasattr(child, 'GetValuePattern'):
                            vp = child.GetValuePattern()
                            if vp and vp.Value:
                                val = str(vp.Value)[:50]
                    except: pass
                    r = child.BoundingRectangle
                    e = {
                        "index": len(elems),
                        "control_type": ct,
                        "name": nm[:60], "value": val,
                        "rect": (r.left, r.top, r.right, r.bottom) if r else (0, 0, 0, 0),
                        "is_enabled": child.IsEnabled,
                        "depth": d,
                    }
                    elems.append(e)
                    self._walk_tree(child, elems, maxd, d + 1)
                except: pass
        except: pass

    def _format_tree(self, elems):
        icons = {
            "Button": "[BTN]", "Edit": "[INP]", "Text": "[TXT]",
            "ListItem": "[LI]", "CheckBox": "[CHK]", "ComboBox": "[CBO]",
            "TabItem": "[TAB]", "TreeItem": "[TREE]", "Hyperlink": "[LNK]",
            "MenuItem": "[MENU]", "Window": "[WIN]", "Pane": "[PANE]",
            "Document": "[DOC]", "Group": "[GRP]", "ToolBar": "[TOOL]",
            "StatusBar": "[STAT]", "MenuBar": "[MENUBAR]", "TitleBar": "[TITLE]",
            "ScrollBar": "[SCRL]", "Slider": "[SLID]", "Spinner": "[SPIN]",
            "ProgressBar": "[PROG]", "DataGrid": "[GRID]", "Header": "[HDR]",
            "SplitButton": "[SPBT]", "RadioButton": "[RAD]", "Image": "[IMG]",
            "AppBar": "[APPR]", "SemanticZoom": "[ZOOM]", "Thumb": "[THMB]",
        }
        lines = []
        for e in elems[:50]:
            ic = icons.get(e["control_type"], f"[{e['control_type'][:4]}]")
            st = " v" if e["is_enabled"] else " x"
            nm = e["name"][:40]
            vl = ""
            val = e.get("value", "")
            if val:
                vl = ' = "' + str(val)[:20] + '"'
            lines.append(f"  #{e['index']:>3d} {ic}{st} {nm}{vl}")
        return "\n".join(lines)

    # ---- Mouse ----
    def click(self, x, y, button="left", clicks=1):
        self._check_escape()
        pyautogui.click(x, y, clicks=clicks, button=button)

    def click_element(self, hwnd, idx):
        root = uia.ControlFromHandle(hwnd) if hwnd and user32.IsWindow(hwnd) else uia.GetForegroundControl()
        elems = []
        self._walk_tree(root, elems, 5, 0)
        if idx < len(elems):
            r = elems[idx]["rect"]
            pyautogui.click((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)

    def scroll(self, x, y, sx=0, sy=0):
        pyautogui.moveTo(x, y)
        if sy: pyautogui.scroll(sy)
        if sx: pyautogui.hscroll(sx)

    def drag(self, fx, fy, tx, ty):
        pyautogui.moveTo(fx, fy)
        pyautogui.drag(tx - fx, ty - fy, duration=0.3)

    # ---- Keyboard ----
    def type_text(self, text):
        pyautogui.write(text, interval=0.02)

    def press_key_safe(self, combo: str):
        combo_lower = combo.lower().replace(" ", "")
        for forbidden in FORBIDDEN_KEY_COMBOS:
            if forbidden.replace(" ", "") in combo_lower:
                raise PermissionError(f"Forbidden key combo: {combo}")
        self._check_escape()
        return self.press_key(combo)

    def press_key(self, combo):
        keys = [k.strip().lower() for k in combo.split("+")]
        m = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "win": "win", "cmd": "win"}
        pressed = [m.get(k, k) for k in keys]
        if len(pressed) > 1:
            pyautogui.hotkey(*pressed)
        else:
            pyautogui.press(pressed[0])

    # ---- Combined state (Codex Window2 API) ----
    def get_window_state(self, title_contains="", include_screenshot=True, include_text=False):
        w = self.get_window(title_contains)
        state = WindowState(window=w or WindowInfo())
        if w and include_screenshot:
            try:
                user32.SetForegroundWindow(w.id)
                time.sleep(0.3)
            except: pass
        if include_screenshot:
            state.screenshots = [self.screenshot()]
        if include_text or True:
            state.accessibility = self.get_accessibility_tree(w.id if w else 0)
        return state


computer_use = ComputerUse()


# ═══════════════════════════════════════════════════════════════
# IPC Transport Layer -- Codex-aligned JSON-line subprocess mode
# ═══════════════════════════════════════════════════════════════

class ComputerUseHelperTransport:
    """JSON-line IPC transport -- spawns codex-computer-use compatible helper.
    
    Protocol (matching Codex codex-computer-use.exe):
      Request:  {"id":N,"method":"click","params":{...},"meta":{...}}\n
      Success:  {"id":N,"ok":true,"result":{...}}\n
      Failure:  {"id":N,"ok":false,"error":"..."}\n
      Approval: {"id":N,"ok":false,"approvalRequest":{"app":"...","displayName":"...","riskLevel":"low"}}\n
    """

    def __init__(self, helper_command: list[str] | None = None, timeout_ms: int = 30000):
        self.helper_command = helper_command or [__file__]
        self.timeout_ms = timeout_ms
        self._process: subprocess.Popen | None = None
        self._seq: int = 0
        self._pending: dict[int, tuple] = {}
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._buffer: str = ""
        self._running: bool = False
        self._stderr_buffer: str = ""

    def start(self):
        """Start the helper subprocess"""
        self._process = subprocess.Popen(
            self.helper_command + ["--helper"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        return self

    def stop(self):
        self._running = False
        if self._process:
            try:
                self._process.stdin.write(json.dumps({"id": -1, "method": "close", "params": {}}) + "\n")
                self._process.stdin.flush()
                self._process.wait(timeout=3)
            except:
                self._process.kill()
            self._process = None

    def _reader_loop(self):
        """Read JSON-line responses from helper stdout"""
        while self._running and self._process and self._process.stdout:
            try:
                line = self._process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    msg_id = msg.get("id")
                    if msg_id is not None and msg_id in self._pending:
                        _, resolve, _ = self._pending.pop(msg_id)
                        if msg.get("ok"):
                            resolve(msg.get("result", {}))
                        else:
                            error = msg.get("error", "helper request failed")
                            resolve({"__error__": error, "approvalRequest": msg.get("approvalRequest")})
                except json.JSONDecodeError:
                    pass
            except:
                break

    def request(self, method: str, params: dict, meta: dict | None = None) -> dict:
        """Send a request and wait for response (sync)"""
        if not self._process:
            raise RuntimeError("Helper not started")

        with self._lock:
            self._seq += 1
            seq = self._seq
            result_holder = {}

        evt = threading.Event()

        def resolve(result):
            result_holder["value"] = result
            evt.set()

        self._pending[seq] = (None, resolve, None)

        request = {"id": seq, "method": method, "params": params}
        if meta:
            request["meta"] = meta

        self._process.stdin.write(json.dumps(request) + "\n")
        self._process.stdin.flush()

        if not evt.wait(timeout=self.timeout_ms / 1000):
            with self._lock:
                self._pending.pop(seq, None)
            raise TimeoutError(f"Computer Use request timed out: {method}")

        return result_holder.get("value", {})

    async def request_async(self, method: str, params: dict, meta: dict | None = None) -> dict:
        """Async version of request"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.request(method, params, meta))


def run_helper_loop():
    """Run as a JSON-line IPC helper (subprocess mode)"""
    import sys
    cu = ComputerUse()
    method_map = {
        "list_windows": lambda p: [w.to_dict() for w in cu.list_windows()],
        "list_apps": lambda p: cu.list_apps(),
        "get_window": lambda p: cu.get_window(hwnd=p.get("id", 0)).to_dict(),
        "get_window_state": lambda p: cu.get_window_state(
            title_contains=p.get("window", {}).get("title", ""),
            include_screenshot=p.get("include_screenshot", True),
            include_text=p.get("include_text", False),
        ).to_dict(),
        "get_screenshot": lambda p: [cu.screenshot().to_dict()],
        "click": lambda p: cu.click(p["x"], p["y"], p.get("mouse_button", "left"), p.get("click_count", 1)),
        "click_element": lambda p: cu.click_element(p.get("window", {}).get("id", 0), p["element_index"]),
        "scroll": lambda p: cu.scroll(p["x"], p["y"], p.get("scrollX", 0), p.get("scrollY", 0)),
        "drag": lambda p: cu.drag(p["from_x"], p["from_y"], p["to_x"], p["to_y"]),
        "press_key": lambda p: cu.press_key(p["key"]),
        "type_text": lambda p: cu.type_text(p["text"]),
        "set_value": lambda p: None,  # stub
        "perform_secondary_action": lambda p: None,  # stub
        "launch_app": lambda p: os.startfile(p["app"]) if os.path.exists(p["app"]) else None,
        "activate_window": lambda p: user32.SetForegroundWindow(p.get("window", {}).get("id", 0)),
        "close": lambda p: None,
        "end_turn": lambda p: None,
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method == "close":
            print(json.dumps({"id": req_id, "ok": True, "result": {}}))
            sys.stdout.flush()
            break

        try:
            handler = method_map.get(method)
            if handler:
                result = handler(params)
                if result is None:
                    result = {}
                print(json.dumps({"id": req_id, "ok": True, "result": result}))
            else:
                print(json.dumps({"id": req_id, "ok": False, "error": f"Unknown method: {method}"}))
        except InterruptedError as e:
            print(json.dumps({"id": req_id, "ok": False, "error": str(e)}))
        except Exception as e:
            print(json.dumps({"id": req_id, "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}))
        sys.stdout.flush()


if __name__ == "__main__":
    import sys
    if "--helper" in sys.argv:
        run_helper_loop()
