"""Tavily web search integration for enriched AI responses.

Provides real-time web search results optimized for financial/stock queries.
Requires: pip install tavily-python
Env var:  TAVILY_API_KEY
"""

import os
import httpx

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT = 12.0


def search_web(query: str, max_results: int = 5) -> dict:
    """Search the web using Tavily API and return structured results.

    Returns a dict with 'results' list, each containing title, url, and content snippet.
    Optimized for financial and stock market queries.
    """
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key or api_key == "tvly-YOUR_KEY_HERE":
        return {"error": "Tavily API key not configured. Set TAVILY_API_KEY in .env"}

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "include_answer": True,
        "max_results": max_results,
        "include_domains": [],
        "exclude_domains": [],
    }

    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.post(_TAVILY_URL, json=payload)
        r.raise_for_status()
        data = r.json()

    results = []
    for item in data.get("results", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "content": item.get("content", "")[:500],
        })

    return {
        "answer": data.get("answer", ""),
        "results": results,
    }
