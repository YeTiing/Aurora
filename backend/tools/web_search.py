# web_search — Codex同款网页搜索工具
from __future__ import annotations
import json, re, aiohttp, asyncio, time
from typing import Any
from urllib.parse import quote_plus
from .base import ToolSpec, ToolCallResult

WEB_SEARCH_SPEC = ToolSpec(
    name="web_search",
    description="Search the web for information. Use when you need up-to-date information, documentation lookups, or data not available in the codebase.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query"
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (default 5, max 10)",
                "default": 5
            },
            "search_type": {
                "type": "string",
                "enum": ["general", "code", "docs"],
                "description": "Type of search: general web, code-specific, or documentation",
                "default": "general"
            }
        },
        "required": ["query"]
    },
    category="information",
    exposure="direct",
    timeout_ms=15000,
)

# 内置搜索回退 — 当无API时用Bing搜索页面结构提取
SEARCH_URLS = {
    "general": "https://www.bing.com/search?q={query}&count={count}",
    "code": "https://www.bing.com/search?q={query}+site:github.com+OR+site:stackoverflow.com&count={count}",
    "docs": "https://www.bing.com/search?q={query}+documentation&count={count}",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


async def web_search_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    query = arguments.get("query", "")
    num_results = min(arguments.get("num_results", 5), 10)
    search_type = arguments.get("search_type", "general")

    if not query.strip():
        return ToolCallResult(id="", name="web_search", output="", success=False, error="Empty query")

    try:
        results = await _search_bing(query, num_results, search_type)
        if not results:
            return ToolCallResult(id="", name="web_search", output="No results found.", success=True)

        output = f"## Web Search: {query}\n\n"
        for i, r in enumerate(results, 1):
            output += f"**{i}.** [{r['title']}]({r['url']})\n"
            output += f"  {r['snippet']}\n"
            if r.get("date"):
                output += f"  *{r['date']}*\n"
            output += "\n"

        return ToolCallResult(
            id="", name="web_search", output=output[:8192], success=True,
            metadata={"query": query, "results_count": len(results), "search_type": search_type}
        )
    except Exception as e:
        return ToolCallResult(id="", name="web_search", output="", success=False,
                               error=f"Search failed: {str(e)[:500]}")


async def _search_bing(query: str, count: int, search_type: str) -> list[dict]:
    """从Bing搜索结果页面提取结构化数据"""
    url = SEARCH_URLS.get(search_type, SEARCH_URLS["general"]).format(
        query=quote_plus(query.encode("utf-8")), count=count
    )

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}) as resp:
                if resp.status != 200:
                    return []

                html = await resp.text(encoding="utf-8", errors="replace")

        results = _parse_bing_results(html, count)
        return results
    except asyncio.TimeoutError:
        return []
    except Exception:
        return []


def _parse_bing_results(html: str, count: int) -> list[dict]:
    """解析Bing搜索结果HTML"""
    results = []

    # 提取搜索结果的匹配项
    # Bing的结果结构: <li class="b_algo"> 包含 <h2><a>, <p> 摘要, <span class="news_dt">日期
    result_blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL)

    for block in result_blocks[:count]:
        title_match = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        date_match = re.search(r'<span class="news_dt"[^>]*>(.*?)</span>', block)

        if title_match:
            url = title_match.group(1)
            title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
            snippet = ""
            if snippet_match:
                snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                snippet = re.sub(r'\s+', ' ', snippet)
            if not title or not url:
                continue

            result = {"title": title, "url": url, "snippet": snippet[:300]}
            if date_match:
                result["date"] = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
            results.append(result)

    return results