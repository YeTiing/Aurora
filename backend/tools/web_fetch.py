# web_fetch 工具 — HTTP 请求 + 域名白名单
from __future__ import annotations
import asyncio, json, re
from urllib.parse import urlparse
from .base import ToolSpec, truncate_output

WEB_FETCH_SPEC = ToolSpec(
    name="web_fetch",
    description="Fetch content from a URL. Supports GET and POST with JSON body. Returns response body as text. Domain whitelist enforced for security.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "method": {"type": "string", "enum": ["GET", "POST"], "description": "HTTP method"},
            "headers": {"type": "object", "description": "Custom HTTP headers"},
            "body": {"type": "string", "description": "Request body (for POST)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 15)"},
        },
        "required": ["url"],
    },
    category="network",
)

# 允许的域名模式
ALLOWED_DOMAINS = [
    r"^.*\.github\.com$", r"^.*\.githubusercontent\.com$",
    r"^.*\.gitlab\.com$", r"^.*\.npmjs\.com$", r"^.*\.npmjs\.org$",
    r"^.*\.pypi\.org$", r"^.*\.python\.org$", r"^.*\.pythonhosted\.org$",
    r"^.*\.crates\.io$", r"^.*\.docs\.rs$",
    r"^.*\.stackoverflow\.com$", r"^.*\.stackexchange\.com$",
    r"^docs\..*", r"^api\..*",
    r"^localhost$", r"^127\.0\.0\.1$", r"^0\.0\.0\.0$",
]

def _is_domain_allowed(url: str) -> bool:
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return False
        for pattern in ALLOWED_DOMAINS:
            if re.match(pattern, hostname):
                return True
        return False
    except Exception:
        return False

async def web_fetch_handler(arguments: dict, workspace: str = ".") -> str:
    url = arguments.get("url", "")
    method = arguments.get("method", "GET").upper()
    headers = arguments.get("headers", {})
    body = arguments.get("body", "")
    timeout = arguments.get("timeout", 15)

    if not url:
        return "Error: No URL provided"

    if not _is_domain_allowed(url):
        parsed = urlparse(url)
        return f"Error: Domain '{parsed.hostname}' is not in the allowed list. Allowed: GitHub, GitLab, PyPI, npm, crates.io, StackOverflow, docs sites, localhost."

    try:
        import aiohttp
        connector = aiohttp.TCPConnector(limit=5, force_close=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            req_headers = {
                "User-Agent": "Aurora-Agent/0.1",
                "Accept": "text/html,application/json,text/plain,*/*",
                **headers,
            }
            kwargs = {"headers": req_headers, "timeout": aiohttp.ClientTimeout(total=timeout)}
            if method == "POST" and body:
                kwargs["data"] = body

            async with session.request(method, url, **kwargs) as resp:
                content_type = resp.headers.get("Content-Type", "")
                text = await resp.text()

                # 根据内容类型处理
                if "application/json" in content_type:
                    try:
                        data = json.loads(text)
                        return json.dumps(data, indent=2, ensure_ascii=False)[:16000]
                    except json.JSONDecodeError:
                        pass

                # HTML 提取文本
                if "text/html" in content_type:
                    text = _extract_text_from_html(text)

                return truncate_output(text, 16000)

    except asyncio.TimeoutError:
        return f"Error: Request to {url} timed out after {timeout}s"
    except Exception as e:
        return f"Error fetching {url}: {type(e).__name__}: {str(e)[:500]}"

def _extract_text_from_html(html: str) -> str:
    """简单 HTML 文本提取"""
    # 移除 script 和 style
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # 移除标签
    text = re.sub(r'<[^>]+>', ' ', html)
    # 清理空白
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()[:16000]