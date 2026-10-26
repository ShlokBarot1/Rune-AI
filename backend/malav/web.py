"""Web helpers shared by Malav's agents: Jina Reader, search (Tavily/Jina), Meta Ad Library, colour sniffing."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from urllib.parse import urljoin

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


def image_search(query: str, n: int = 8) -> list[str]:
    """Image URLs from Tavily image search; [] without a key. `site:pinterest.com` returns pins (i.pinimg.com)."""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return []
    r = httpx.post("https://api.tavily.com/search", timeout=40, headers={"Authorization": f"Bearer {key}"},
                   json={"query": query, "max_results": n, "include_images": True})
    r.raise_for_status()
    return [i if isinstance(i, str) else i["url"] for i in r.json().get("images", [])]


def ad_library_token() -> str | None:
    """A real person's token (ID-confirmed, Ad Library terms accepted). The sandbox account's owner token can't read
    the Ad Library, so META_AD_LIBRARY_TOKEN is separate; falls back to META_ACCESS_TOKEN."""
    return os.getenv("META_AD_LIBRARY_TOKEN") or os.getenv("META_ACCESS_TOKEN")


def ad_library(terms: str, countries: list[str], limit: int = 8) -> list[dict]:
    """Meta Ad Library (ads_archive). Commercial ads are only returned for EU delivery, hence EU countries."""
    v = os.getenv("META_API_VERSION", "v23.0")
    # token in a header, not the URL: httpx errors (shown in the agent log) print the URL
    r = httpx.get(f"https://graph.facebook.com/{v}/ads_archive", timeout=30,
                  headers={"Authorization": f"Bearer {ad_library_token()}"}, params={
        "search_terms": terms, "ad_type": "ALL",
        "ad_reached_countries": json.dumps(countries), "limit": limit,
        "fields": "page_name,ad_creative_bodies,ad_creative_link_titles,ad_delivery_start_time"})
    if r.status_code >= 400:  # Meta's own message says what's missing (identity confirmation, app role, ...)
        err = (r.json() if r.content else {}).get("error") or {}
        raise RuntimeError(f"Ad Library {r.status_code}: {err.get('message', r.text[:200])} (code {err.get('code')}/{err.get('error_subcode')})")
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


def geocode(query: str) -> tuple[float, float] | None:
    """(lat, lng) for an address via OpenStreetMap Nominatim (free; policy: 1 request/s, a real User-Agent).
    None when not found."""
    r = httpx.get("https://nominatim.openstreetmap.org/search", params={"q": query, "format": "json", "limit": 1},
                  headers={"User-Agent": "RuneAI/1.0 (ad targeting; contact via the site)"}, timeout=20)
    r.raise_for_status()
    hits = r.json()
    return (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None


# ---------- brand identity: the real logo, colours and fonts ----------

# web-framework defaults that pages carry but aren't the brand's (Bootstrap 5)
FRAMEWORK_COLOURS = {"#0D6EFD", "#6C757D", "#198754", "#0DCAF0", "#FFC107", "#DC3545", "#F8F9FA", "#212529",
                     "#6610F2", "#6F42C1", "#D63384", "#FD7E14", "#20C997", "#0A58CA", "#157347", "#BB2D3B"}
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = lambda tag, name: (re.search(rf"""\b{name}=["']([^"']+)["']""", tag, re.I) or [None, ""])[1]
_GENERIC_FONTS = {"sans-serif", "serif", "monospace", "system-ui", "inherit", "initial", "arial", "helvetica",
                  "helvetica neue", "segoe ui", "roboto", "-apple-system", "blinkmacsystemfont", "times new roman",
                  "var", "ui-sans-serif", "apple color emoji", "segoe ui emoji", "noto color emoji"}


def _logo_candidates(html_: str, base: str, brand: str) -> list[str]:
    words = {w for w in re.findall(r"[a-z]{3,}", brand.lower())}
    scored = []
    for i, tag in enumerate(_IMG.findall(html_)[:80]):
        src = _ATTR(tag, "src") or _ATTR(tag, "srcset").split(" ")[0] or _ATTR(tag, "data-src")
        if not src or src.startswith("data:"):
            continue
        blob = f"{tag} {src}".lower()
        score = (3 * ("logo" in blob) + 2 * bool(words & set(re.findall(r"[a-z]{3,}", _ATTR(tag, "alt").lower() + src.lower())))
                 + 2 * bool(re.search(r"header|navbar|nav-|site-logo|brand", blob)) + (".svg" in src.lower())
                 - 4 * bool(re.search(r"footer|partner|payment|award|press|client|badge|icon-", blob)) - i * 0.02)
        if score >= 3:
            scored.append((score, urljoin(base, src.replace("&amp;", "&"))))
    for tag in re.findall(r"<link\b[^>]*rel=[\"'][^\"']*(?:apple-touch-icon|icon)[^\"']*[\"'][^>]*>", html_, re.I):
        href = _ATTR(tag, "href")
        if href and not href.endswith(".ico"):
            scored.append((1 + ("apple" in tag.lower()), urljoin(base, href)))
    return [u for _, u in sorted(scored, key=lambda x: -x[0])]


def _svg_colours(svg: str) -> list[str]:
    found = re.findall(r"(?:fill|stroke|stop-color)\s*[:=]\s*[\"']?\s*(#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})\b", svg)
    return [c for c, _ in Counter(_norm(h) for h in found).most_common()]


def _raster_colours(raw: bytes) -> list[str]:
    from io import BytesIO
    from PIL import Image
    with Image.open(BytesIO(raw)) as im:
        im = im.convert("RGBA").resize((96, 96))
        px = [p[:3] for p in im.getdata() if p[3] > 200]
    if not px:
        return []
    q = Image.new("RGB", (len(px), 1))
    q.putdata(px)
    pal = q.quantize(colors=6)
    counts = sorted(pal.getcolors(), reverse=True)
    rgb = pal.getpalette()
    return [_norm("#%02X%02X%02X" % tuple(rgb[i * 3:i * 3 + 3])) for _, i in counts]


def _brandish(colours: list[str]) -> list[str]:
    """Drop near-white / near-black / grey: the colours that make a brand recognisable are the saturated ones."""
    import colorsys
    out = []
    for c in colours:
        r, g, b = (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        if 0.08 < l < 0.93 and s > 0.25 and c not in out:
            out.append(c)
    return out


def _fonts(html_: str) -> list[str]:
    out = [f.replace("+", " ") for f in re.findall(r"fonts\.googleapis\.com/css2?\?family=([^&:\"'@;]+)", html_)]
    for decl in re.findall(r"font-family\s*:\s*([^;}{<]+)", html_)[:60]:
        for f in decl.split(","):
            f = f.replace("!important", "").strip().strip("'\"")
            if f and f.lower() not in _GENERIC_FONTS and not f.lower().startswith(("var(", "--")) and len(f) < 40:
                out.append(f)
    return list(dict.fromkeys(out))[:3]


def brand_identity(url: str, brand: str, out_dir) -> dict:
    """The brand's real logo (saved under out_dir), its colours (from the logo, else the site's theme colour, never
    a web framework's palette) and fonts. Empty fields when not found. Never raises."""
    from pathlib import Path
    out: dict = {"logo": None, "colors": [], "fonts": []}
    try:
        html_ = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (RuneAI)"}, timeout=25, follow_redirects=True).text
    except Exception:
        return out
    out["fonts"] = _fonts(html_)
    logo_colours: list[str] = []
    for src in _logo_candidates(html_, url, brand)[:4]:
        try:
            r = httpx.get(src, headers={"User-Agent": "Mozilla/5.0 (RuneAI)"}, timeout=25, follow_redirects=True)
            r.raise_for_status()
            svg = "svg" in r.headers.get("content-type", "") or src.lower().split("?")[0].endswith(".svg")
            if not svg:
                from io import BytesIO
                from PIL import Image
                with Image.open(BytesIO(r.content)) as im:
                    if max(im.size) < 100:  # a favicon blown up looks worse than the brand name in type
                        continue
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            path = Path(out_dir) / f"logo{'.svg' if svg else '.png'}"
            if svg:
                path.write_bytes(r.content)
                logo_colours = _svg_colours(r.text)
            else:
                from io import BytesIO
                from PIL import Image
                with Image.open(BytesIO(r.content)) as im:
                    im.convert("RGBA").save(path)
                logo_colours = _raster_colours(r.content)
            out["logo"] = str(path)
            break
        except Exception:
            continue
    if not out["logo"]:  # logos drawn inline: <a class="logo"><svg>…</svg></a> or <svg class="…logo…">
        m = (re.search(r"<a\b[^>]*(?:logo|header__heading|site-header|home)[^>]*>\s*(?:<[^>]+>\s*){0,3}(<svg\b.*?</svg>)", html_, re.I | re.S)
             or re.search(r"(<svg\b[^>]*(?:logo)[^>]*>.*?</svg>)", html_, re.I | re.S))
        if m and len(m.group(1)) < 200_000:
            svg = m.group(1)
            if "xmlns=" not in svg[:300]:
                svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "logo.svg").write_text(svg, encoding="utf-8")
            out["logo"], logo_colours = str(Path(out_dir) / "logo.svg"), _svg_colours(svg)
    theme = [_norm(t) for t in _THEME.findall(html_)]
    css = [c for c in _brandish([_norm(h) for h, _ in Counter(_CSS_HEX.findall(html_)).most_common(20)])
           if c not in FRAMEWORK_COLOURS]
    out["colors"] = list(dict.fromkeys(_brandish(logo_colours) + _brandish(theme) + css))[:3]
    return out
