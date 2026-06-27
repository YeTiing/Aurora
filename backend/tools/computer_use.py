# computer_use 工具 -- 暴露给Agent的桌面自动化接口（对齐Codex 14个方法）
from __future__ import annotations
from typing import Any
from backend.computer_use.engine import computer_use, WindowState
from .base import ToolSpec, ToolCallResult, truncate_output

COMPUTER_USE_SPEC = ToolSpec(
    name="computer_use",
    description=(
        "Control the Windows desktop. Use to open apps, click, type, scroll, "
        "capture screenshots, and get window + accessibility info. "
        "Works like Codex Computer Use agent -- the agent sees the screen and controls it.\n\n"
        "Methods:\n"
        "- get_window_state: capture screenshot + accessibility tree of a window\n"
        "- screenshot: take full screen screenshot\n"
        "- list_windows: list all visible windows\n"
        "- get_window: get window info by title\n"
        "- activate_window: bring window to front\n"
        "- launch_app: launch application by name\n"
        "- click: click at x,y coordinates\n"
        "- click_element: click UIA element by index\n"
        "- scroll: scroll at position\n"
        "- drag: drag from/to coordinates\n"
        "- type_text: type text\n"
        "- press_key: press key combo (e.g. Ctrl+C)\n"
        "- get_accessibility_tree: get UIA accessibility tree"
    ),
    parameters={
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "description": "Method to call",
                "enum": [
                    "get_window_state", "screenshot", "list_windows", "get_window",
                    "activate_window", "launch_app", "click", "click_element",
                    "scroll", "drag", "type_text", "press_key", "get_accessibility_tree"
                ]
            },
            "title": {"type": "string", "description": "Window title to search/focus"},
            "x": {"type": "integer", "description": "X coordinate"},
            "y": {"type": "integer", "description": "Y coordinate"},
            "button": {"type": "string", "default": "left", "enum": ["left","right","middle"]},
            "clicks": {"type": "integer", "default": 1},
            "element_index": {"type": "integer", "description": "UIA element index to click"},
            "scroll_x": {"type": "integer", "default": 0},
            "scroll_y": {"type": "integer", "default": 0},
            "to_x": {"type": "integer"},
            "to_y": {"type": "integer"},
            "text": {"type": "string", "description": "Text to type"},
            "key_combo": {"type": "string", "description": "Key combo like Ctrl+C or Alt+Tab"},
            "app_name": {"type": "string", "description": "App to launch (e.g. chrome, notepad)"},
            "include_screenshot": {"type": "boolean", "default": True},
            "save_screenshot_to": {"type": "string", "description": "Path to save screenshot file"},
        },
        "required": ["method"]
    },
    category="computer_use",
    exposure="direct",
    timeout_ms=30000,
)

async def computer_use_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    method = arguments.get("method", "")
    try:
        cu = computer_use

        if method == "screenshot":
            path = arguments.get("save_screenshot_to", "")
            if path:
                filepath = cu.screenshot_to_file(path)
                return ToolCallResult(id="", name="computer_use",
                    output=f"Screenshot saved to: {filepath}", success=True,
                    metadata={"screenshot_path": filepath})

            ss = cu.screenshot()
            return ToolCallResult(id="", name="computer_use",
                output=f"Screenshot captured ({ss.width}x{ss.height})",
                success=True, metadata={"width": ss.width, "height": ss.height})

        elif method == "list_windows":
            windows = cu.list_windows()
            lines = [f"  [{i}] {w.title[:50]} ({w.app}) {'[ACTIVE]' if w.is_active else ''}"
                     for i, w in enumerate(windows[:15])]
            return ToolCallResult(id="", name="computer_use",
                output=f"{len(windows)} windows:\n" + "\n".join(lines),
                success=True, metadata={"count": len(windows)})

        elif method == "get_window":
            title = arguments.get("title", "")
            w = cu.get_window(title)
            if not w:
                return ToolCallResult(id="", name="computer_use",
                    output=f"Window not found: {title}", success=False)
            return ToolCallResult(id="", name="computer_use",
                output=f"Window: {w.title} ({w.app}) rect={w.rect} active={w.is_active}",
                success=True, metadata={"title": w.title, "app": w.app, "id": w.id})

        elif method == "activate_window":
            title = arguments.get("title", "")
            cu.activate_window(title)
            return ToolCallResult(id="", name="computer_use",
                output=f"Activated: {title}", success=True)

        elif method == "launch_app":
            app = arguments.get("app_name", "")
            cu.launch_app(app)
            return ToolCallResult(id="", name="computer_use",
                output=f"Launched: {app}", success=True)

        elif method == "click":
            x, y = arguments.get("x", 0), arguments.get("y", 0)
            btn = arguments.get("button", "left")
            clicks = arguments.get("clicks", 1)
            cu.click(x, y, btn, clicks)
            return ToolCallResult(id="", name="computer_use",
                output=f"Clicked ({x},{y}) {btn} x{clicks}", success=True)

        elif method == "click_element":
            idx = arguments.get("element_index", 0)
            title = arguments.get("title", "")
            hwnd = 0
            if title:
                w = cu.get_window(title)
                if w: hwnd = w.id
            cu.click_element(hwnd, idx)
            return ToolCallResult(id="", name="computer_use",
                output=f"Clicked element #{idx}", success=True)

        elif method == "scroll":
            x, y = arguments.get("x", 0), arguments.get("y", 0)
            cu.scroll(x, y, arguments.get("scroll_x", 0), arguments.get("scroll_y", 0))
            return ToolCallResult(id="", name="computer_use",
                output=f"Scrolled at ({x},{y})", success=True)

        elif method == "drag":
            cu.drag(arguments.get("x",0), arguments.get("y",0),
                    arguments.get("to_x",0), arguments.get("to_y",0))
            return ToolCallResult(id="", name="computer_use",
                output="Drag complete", success=True)

        elif method == "type_text":
            text = arguments.get("text", "")
            cu.type_text(text)
            return ToolCallResult(id="", name="computer_use",
                output=f"Typed: {text[:50]}", success=True)

        elif method == "press_key":
            combo = arguments.get("key_combo", "")
            cu.press_key(combo)
            return ToolCallResult(id="", name="computer_use",
                output=f"Pressed: {combo}", success=True)

        elif method == "get_accessibility_tree":
            title = arguments.get("title", "")
            hwnd = 0
            if title:
                w = cu.get_window(title)
                if w: hwnd = w.id
            tree = cu.get_accessibility_tree(hwnd)
            return ToolCallResult(id="", name="computer_use",
                output=f"Accessibility tree ({tree['element_count']} elements):\n{tree['tree']}",
                success=True, metadata=tree)

        elif method == "get_window_state":
            title = arguments.get("title", "")
            inc_ss = arguments.get("include_screenshot", True)
            state = cu.get_window_state(title, include_screenshot=inc_ss)
            d = cu.to_dict(state)
            output = f"Window: {d['window']['title']} ({d['window']['app']})\n"
            if d["screenshots"]:
                output += f"Screenshot: {d['screenshots'][0]['width']}x{d['screenshots'][0]['height']}\n"
            output += f"Accessibility ({d['accessibility'].get('element_count',0)} elements):\n"
            output += d["accessibility"].get("tree", "")
            return ToolCallResult(id="", name="computer_use",
                output=truncate_output(output, 8000), success=True, metadata=d)

        else:
            return ToolCallResult(id="", name="computer_use",
                output="", error=f"Unknown method: {method}", success=False)

    except Exception as e:
        return ToolCallResult(id="", name="computer_use",
            output="", error=f"{type(e).__name__}: {str(e)[:300]}", success=False)