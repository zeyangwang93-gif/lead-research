"""统一的搜索接口：search(query, max_results) -> [{url, title, snippet}]。
目前只实现 Tavily（REST：POST https://api.tavily.com/search，Bearer 认证，basic 深度每次 1 credit）。"""
import os

import httpx

import config


def search(query: str, max_results: int | None = None) -> list[dict]:
    max_results = max_results or config.SEARCH_MAX_RESULTS
    if config.SEARCH_PROVIDER == "tavily":
        return _tavily(query, max_results)
    raise ValueError(f"未知的 SEARCH_PROVIDER: {config.SEARCH_PROVIDER}")


def _tavily(query: str, max_results: int) -> list[dict]:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("缺少 TAVILY_API_KEY（请写入 .env）")
    resp = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": query, "max_results": min(max_results, 20), "search_depth": "basic"},
        timeout=30,
    )
    resp.raise_for_status()
    return [
        {"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("content", "")}
        for r in resp.json().get("results", [])
        if r.get("url")
    ]
