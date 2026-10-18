"""Web helpers shared by Malav's agents: Jina Reader, search (Tavily/Jina), Meta Ad Library, colour sniffing."""
from __future__ import annotations

import json
import os
import re
from collections import Counter

import httpx

UA = {"User-Agent": "Mozilla/5.0 (RuneAI research bot)"}
# a hex colour used as a CSS value (`color:#1f2d4d`, `fill: #abc`), not an anchor like href="#add"
_CSS_HEX = re.compile(r"(?<=[:\s,(])#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![\w-])")
_THEME = re.compile(r"""<meta[^>]+name=["']theme-color["'][^>]+content=["'](#[0-9a-fA-F]{3,6})""", re.I)


def read_page(url: str, max_chars: int = 15_000) -> str:
    """Page as LLM-friendly markdown via Jina Reader (https://r.jina.ai/<url>)."""
    headers = dict(UA)
    if os.getenv("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {os.getenv('JINA_API_KEY')}"
    r = httpx.get(f"https://r.jina.ai/{url}", headers=headers, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r.text[:max_chars]


def search(query: str, n: int = 5) -> list[dict]:
    """[{url, title, content}] from Tavily, falling back to Jina Search. [] when neither key is set."""
    if os.getenv("TAVILY_API_KEY"):
        key = os.getenv("TAVILY_API_KEY")
        r = httpx.post("https://api.tavily.com/search", timeout=30, headers={"Authorization": f"Bearer {key}"},
                       json={"api_key": key, "query": query, "max_results": n, "search_depth": "basic"})
        r.raise_for_status()
        return [{"url": x["url"], "title": x.get("title", ""), "content": x.get("content", "")}
                for x in r.json().get("results", [])]
    if os.getenv("JINA_API_KEY"):
        r = httpx.get("https://s.jina.ai/", params={"q": query}, timeout=60, headers={
            "Authorization": f"Bearer {os.getenv('JINA_API_KEY')}", "Accept": "application/json",
            "X-Respond-With": "no-content"})
        r.raise_for_status()
        return [{"url": x["url"], "title": x.get("title", ""), "content": x.get("description") or x.get("content", "")}
                for x in (r.json().get("data") or [])[:n]]
    return []


def ad_library(terms: str, countries: list[str], limit: int = 8) -> list[dict]:
    """Meta Ad Library (ads_archive). Commercial ads are only returned for EU delivery, hence EU countries."""
    v = os.getenv("META_API_VERSION", "v23.0")
    r = httpx.get(f"https://graph.facebook.com/{v}/ads_archive", timeout=30, params={
        "access_token": os.getenv("META_ACCESS_TOKEN"), "search_terms": terms, "ad_type": "ALL",
        "ad_reached_countries": json.dumps(countries), "limit": limit,
        "fields": "page_name,ad_creative_bodies,ad_creative_link_titles,ad_delivery_start_time"})
    r.raise_for_status()
    return r.json().get("data", [])


_OG = re.compile(r"""<meta[^>]+(?:property|name)=["']og:image(?::url)?["'][^>]+content=["']([^"']+)""", re.I)
_OG_REV = re.compile(r"""<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']og:image(?::url)?["']""", re.I)


def og_image(url: str) -> str | None:
    """The page's share image (og:image): on a product page this is almost always the product shot."""
    html = httpx.get(url, headers=UA, timeout=20, follow_redirects=True).text
    m = _OG.search(html) or _OG_REV.search(html)
    if not m:
        return None
    img = m.group(1).replace("&amp;", "&")
    return "https:" + img if img.startswith("//") else img


def _norm(h: str) -> str:
    h = h.lstrip("#")
    return "#" + (h if len(h) == 6 else "".join(c * 2 for c in h)).upper()


def page_colors(url: str, top: int = 6) -> list[str]:
    """Most frequent non-grey CSS hex colours in the raw HTML; theme-color first. Brand-colour candidates."""
    html = httpx.get(url, headers=UA, timeout=20, follow_redirects=True).text
    counts = Counter(_norm(h) for h in _CSS_HEX.findall(html))
    for h in list(counts):
        if h[1:3] == h[3:5] == h[5:7]:  # white, black and greys say nothing about the brand
            del counts[h]
    theme = [_norm(t) for t in _THEME.findall(html)]
    return list(dict.fromkeys(theme + [h for h, _ in counts.most_common(top)]))[:top]
