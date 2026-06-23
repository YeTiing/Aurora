# browser_use 工具 — 注册为Agent工具
from __future__ import annotations
from .base import ToolSpec, ToolCallResult

BROWSER_USE_SPEC = ToolSpec(
    name="browser_use",
    description="Control Chrome browser via CDP. Navigate pages, take screenshots, click elements, type text, get HTML.",
    parameters={
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": ["navigate","screenshot","click","type","get_html","list_pages","ensure"]},
            "url": {"type": "string"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["method"]
    },
    category="browser",
    exposure="direct",
    timeout_ms=30000,
)

async def browser_use_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    method = arguments.get("method", "")
    try:
        from backend.browser_use import browser_use
        bu = browser_use

        if method == "ensure":
            ok = await bu.ensure_browser()
            return ToolCallResult(id="", name="browser_use",
                output=f"Browser {'started' if ok else 'failed'}", success=ok)

        elif method == "navigate":
            await bu.ensure_browser()
            url = arguments.get("url", "https://google.com")
            result = await bu.navigate(url)
            return ToolCallResult(id="", name="browser_use",
                output=f"Navigated to {url}", success=True, metadata=result)

        elif method == "screenshot":
            await bu.ensure_browser()
            result = await bu.screenshot()
            return ToolCallResult(id="", name="browser_use",
                output=f"Screenshot captured", success=result.get("error") is None,
                metadata={"has_data": "data_url" in result})

        elif method == "click":
            await bu.ensure_browser()
            sel = arguments.get("selector", "body")
            result = await bu.click(sel)
            return ToolCallResult(id="", name="browser_use",
                output=f"Clicked: {sel}", success=result.get("error") is None,
                metadata=result)

        elif method == "type":
            await bu.ensure_browser()
            sel = arguments.get("selector", "input")
            text = arguments.get("text", "")
            result = await bu.type_text(sel, text)
            return ToolCallResult(id="", name="browser_use",
                output=f"Typed '{text[:30]}' into {sel}", success=True)

        elif method == "get_html":
            await bu.ensure_browser()
            html = await bu.get_html()
            return ToolCallResult(id="", name="browser_use",
                output=truncate_output(html, 5000), success=True,
                metadata={"length": len(html)})

        elif method == "list_pages":
            pages = await bu.list_pages()
            output = "\n".join(f"  [{p.title[:30]}] {p.url[:60]}" for p in pages)
            return ToolCallResult(id="", name="browser_use",
                output=f"{len(pages)} pages:\n{output}", success=True)

        return ToolCallResult(id="", name="browser_use", output="", error=f"Unknown: {method}", success=False)

    except Exception as e:
        return ToolCallResult(id="", name="browser_use", output="",
                               error=f"{type(e).__name__}: {str(e)[:200]}", success=False)