"""Web reading helpers shared by Malav's agents (Jina Reader + raw HTML colour sniffing)."""
from __future__ import annotations

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
