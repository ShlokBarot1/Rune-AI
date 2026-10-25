"""Range campaigns (ClientBrief.creative_focus == "range"): find the brand's range on its site, pick what to show,
and turn product facts into short benefit tags for the line-up image and the carousel cards.

Best effort by design: product listing pages differ per site. Whatever isn't found, intake asks the human for
(one photo per product), so a range campaign never depends on scraping working.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel

from core import bus, llm
from core.schemas import ClientBrief, Product
from malav import media, web
from malav.hooks import policy_issues

AGENT = "malav.intake"
MAX_SHOWN = 9          # carousel cards (Meta allows 2-10, the cover is one)
MAX_LINEUP = 4         # products in one line-up image: more and each packshot gets too small to read
SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "AUD": "A$", "CAD": "C$", "SGD": "S$",
          "AED": "AED ", "NZD": "NZ$"}
_HREF = re.compile(r"""<a\b[^>]*href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_LISTING = re.compile(r"collection|categor|catalog|browse|range|shop/?$|products/?$|search", re.I)
# pages about the products, not of them
_CONTENT = re.compile(r"/(blogs?|news|articles?|guides?|stories|journal|press|help|faqs?|support|about|careers|"
                      r"policy|policies|terms|privacy|contact|reviews?)(/|$)", re.I)
# one product's page: Shopify /products/x, WooCommerce /product/x, Amazon /dp/x, marketplaces /p/x, /item/x, /x/buy
_ITEM = re.compile(r"/(products?|p|item|dp)/[^/]+/?$|/buy/?$", re.I)


class _Range(BaseModel):
    products: list[Product] = []


RANGE_SYSTEM = """You list the individual products of a brand's range from one of its web pages. For each product:
exact name, price as a number in major units (null unless the page shows it for this product), currency: the ISO
code of that price as shown (INR, USD, EUR...), absolute product page URL, absolute URL of the product photo (a packshot, never a logo, icon or banner), and usp: 1-3 short buying
reasons stated on the page, benefits first: what it does or is made of, a key spec or a proof point (e.g.
"10% niacinamide", "40h battery life", "machine washable", "made with merino wool", "no added sugar"); a size or
pack count only if the page states nothing else. Only real products of the requested range;
at most 10; never invent facts."""
# pack sizes are facts, not reasons to buy: they go last when picking benefit tags
# sizes and counts are facts, not reasons to buy, in any category: "Pack of 6", "50 ml", "Set of 2", "Size M"
_PACK = re.compile(r"\b(pack|set|box|bundle) of\b|\bper (pack|sachet|box)\b|\bsachets?\b|\bpieces?\b|\bpcs\b|"
                   r"\b\d+\s*(ml|l|kg|oz|fl oz|ct|count)\b|\bsizes?\s*[:\-]?\s*(xs|s|m|l|xl|xxl|\d+)\b", re.I)


def site(url: str) -> str:
    """Registrable domain ("shop.brand.co.in" -> "brand.co.in"): brands often sell from a shop subdomain."""
    parts = urlparse(url).netloc.lower().removeprefix("www.").split(".")
    keep = 3 if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "net", "org", "gov", "ac") else 2
    return ".".join(parts[-keep:])


def _stem(w: str) -> str:
    """'mattresses' -> 'mattress', 'shoes' -> 'shoe', 'serums' -> 'serum': hints are plural, URLs often singular."""
    if w.endswith(("sses", "shes", "ches", "xes")):
        return w[:-2]
    return w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def _words(hint: str, url: str) -> set[str]:
    """What the range is about, stemmed. The brand's own name is dropped: it's in every link on its site."""
    return ({_stem(w) for w in re.findall(r"[a-z]{4,}", hint.lower())}
            - {"range", "product", "campaign", "general", "brand", "showcase", "about", "their", "with", "collection"}
            - {_stem(w) for w in re.findall(r"[a-z]{4,}", site(url))})


def _pages(url: str, hint: str, n: int = 3) -> tuple[list[str], list[Product]]:
    """(listing pages to read, product pages found directly) on the brand's domain or its shop subdomains: homepage
    links scored by the hint words, plus a web search (it also finds pages behind JS menus and shops on other
    subdomains). A search hit that is one product's page becomes a product straight away (named by its title)."""
    dom, words = site(url), _words(hint, url)
    ours = lambda link: urlparse(link).netloc.lower().endswith(dom)
    listings: dict[str, int] = {}
    products: dict[str, Product] = {}

    def topical(link: str, text: str = "") -> int:
        return sum(w in f"{urlparse(link).path} {text}".lower() for w in words)
    try:
        html = httpx.get(url, headers=web.UA, timeout=20, follow_redirects=True).text
        for href, text in _HREF.findall(html):
            link = urljoin(url, href)
            hit = topical(link, re.sub(r"<[^>]+>", " ", text))
            if ours(link) and hit and not _ITEM.search(urlparse(link).path) and not _CONTENT.search(urlparse(link).path):
                listings[link] = max(listings.get(link, 0), 3 * hit + bool(_LISTING.search(urlparse(link).path)))
    except Exception:
        pass
    try:
        for r in web.search(f"{' '.join(sorted(words)) or 'products'} site:{dom}", 8):
            link, title = r["url"], r.get("title", "")
            if not ours(link) or not topical(link, title) or _CONTENT.search(urlparse(link).path):
                continue
            if _ITEM.search(urlparse(link).path):
                if not any(w in title.lower() for w in words):  # e.g. a generic "Our Products" page
                    continue
                name, *rest = [x.strip() for x in re.split(r"\s+[|–]\s+|\s-\s|,\s", title) if x.strip()]
                # the title's other parts are facts ("32 g", "Pack of 30"); the page itself is read for the rest
                products.setdefault(name.lower(), Product(name=name[:80], url=link, usp=rest[:2]))
            else:
                listings[link] = listings.get(link, 0) + 3 * topical(link, title) + 2
    except Exception:
        pass
    return [u for u, _ in sorted(listings.items(), key=lambda x: -x[1])][:n], list(products.values())


def _details(p: Product, hint: str) -> Product:
    """Price, facts and photo from the product's own page (search snippets mix products up: never trust a price
    from them). The product as found when the page can't be read."""
    try:
        got = llm.structured("fast", RANGE_SYSTEM, f"Range: {hint}\nThis page is ONE product: {p.name}. Return only "
                                                    f"it.\nPage: {p.url}\n\n{web.read_page(p.url, 8000)}", _Range)
        q = got.products[0]
        return p.model_copy(update={"price": q.price, "usp": q.usp or p.usp, "images": q.images or p.images})
    except Exception:
        return p


_SHOPIFY_CUR = re.compile(r'Shopify\.currency\s*=\s*\{\s*"active"\s*:\s*"([A-Z]{3})"')


def _text(html_: str, n: int = 300) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html_ or "").split())[:n]


def shop_feed(url: str, hint: str = "", limit: int = 24) -> list[Product]:
    """Products straight from the store platform's public product API: Shopify (/products.json) or WooCommerce
    (Store API). Exact names, prices, currency and packshots, no crawling. [] when the site is neither. Products
    matching the hint words first (all of them when none match: the hint may be the brand itself)."""
    root = f"{urlparse(url).scheme or 'https'}://{urlparse(url).netloc}"
    words = _words(hint, url) if hint else set()
    out: list[Product] = []
    try:
        r = httpx.get(f"{root}/products.json", params={"limit": 250}, headers=web.UA, timeout=20, follow_redirects=True)
        if r.status_code == 200 and "products" in r.text[:200]:
            cur = None
            try:
                m = _SHOPIFY_CUR.search(httpx.get(root, headers=web.UA, timeout=20, follow_redirects=True).text)
                cur = m.group(1) if m else None
            except Exception:
                pass
            for x in r.json().get("products", []):
                v = (x.get("variants") or [{}])[0]
                out.append(Product(name=x["title"], url=f"{root}/products/{x['handle']}",
                                   price=float(v["price"]) if v.get("price") else None, currency=cur,
                                   usp=[t for t in [_text(x.get("body_html"), 120)] if t][:1],
                                   images=[i["src"] for i in x.get("images", [])[:2]]))
    except Exception:
        pass
    if not out:
        try:
            r = httpx.get(f"{root}/wp-json/wc/store/v1/products", params={"per_page": 50}, headers=web.UA, timeout=20,
                          follow_redirects=True)
            if r.status_code == 200 and r.text.startswith("["):
                for x in r.json():
                    pr = x.get("prices") or {}
                    minor = int(pr.get("currency_minor_unit", 2))
                    out.append(Product(name=_text(x["name"], 150), url=x.get("permalink"),
                                       price=int(pr["price"]) / 10 ** minor if pr.get("price") else None,
                                       currency=pr.get("currency_code"),
                                       usp=[t for t in [_text(x.get("short_description"), 120)] if t][:1],
                                       images=[i["src"] for i in x.get("images", [])[:2]]))
        except Exception:
            pass
    if words:
        hit = lambda p: any(w in f"{p.name} {p.url}".lower() for w in words)
        out = sorted(out, key=lambda p: not hit(p)) if any(map(hit, out)) else out
    return out[:limit]


def find_range(cid: str, url: str, hint: str, known: list[Product], max_reads: int = 4) -> list[Product]:
    """The range's products with real photos where the site has them, known products first. Never raises.
    Tries the store's own product API first (Shopify / WooCommerce: exact and instant); else reads the best listing
    pages, and when an entry it finds is itself a category page (sites often list categories first), reads that
    one level deeper and drops the category from the products."""
    if url and len(feed := [p for p in shop_feed(url, hint) if p.images]) >= 2:
        names = {p.name.lower() for p in known}
        found = list(known) + [p for p in feed if p.name.lower() not in names][:12]
        bus.log(cid, AGENT, f"Read {len(feed)} products from the store's product catalogue: "
                            + ", ".join(p.name for p in feed[:6]))
        return found
    queue, direct = _pages(url, hint) if url else ([], [])
    found, read, dom = list(known), set(), site(url) if url else ""
    while queue and len(read) < max_reads and len(found) - len(known) < 8:
        page = queue.pop(0)
        if page in read:
            continue
        read.add(page)
        try:
            got = llm.structured("fast", RANGE_SYSTEM, f"Range: {hint}\nPage: {page}\n\n{web.read_page(page)}", _Range)
        except Exception as e:
            bus.log(cid, AGENT, f"Couldn't read the range from {page} ({e.__class__.__name__})")
            continue
        items = [p for p in got.products if p.name]
        bus.log(cid, AGENT, f"Read the range on {page}: {', '.join(p.name for p in items[:8]) or 'no products'}")
        if len(items) >= 2:  # this page was a listing: whatever linked here was a category, not a product
            found = [p for p in found if (p.url or "").rstrip("/") != page.rstrip("/")]
        names = {p.name.lower() for p in found}
        new = [p for p in items if p.name.lower() not in names]
        found += new
        if len(new) < 3:  # maybe categories: look one level deeper
            queue += [p.url for p in new if p.url and urlparse(p.url).netloc.lower().endswith(dom) and p.url not in read]
    names = {p.name.lower() for p in found}
    direct = [p for p in direct if p.name.lower() not in names][:8]
    with ThreadPoolExecutor(max_workers=4) as ex:  # product pages the search found: their own facts and price
        found += list(ex.map(lambda p: _details(p, hint), direct))

    def photo(p: Product) -> Product:
        imgs = [i for i in p.images[:3] if media.product_photo_ok(i)]
        if not imgs and p.url and p.url.rstrip("/") != url.rstrip("/"):
            try:
                og = web.og_image(p.url)
                imgs = [og] if og and media.product_photo_ok(og) else []
            except Exception:
                pass
        return p.model_copy(update={"images": imgs})
    with ThreadPoolExecutor(max_workers=6) as ex:
        found = list(ex.map(photo, found[:12]))
    seen, distinct = set(), []
    for p in found:  # colour / size variants of one model share the same facts: keep the first
        key = tuple(u.lower() for u in p.usp)
        if not key or key not in seen:
            seen.add(key)
            distinct.append(p)
    found = distinct
    bus.log(cid, AGENT, f"Range: {len(found)} product(s), {sum(bool(p.images) for p in found)} with a real photo")
    return found


def shown(brief: ClientBrief) -> list[Product]:
    """The products a range ad shows: those with a photo, in brief order, at most MAX_SHOWN."""
    return [p for p in brief.products if p.images][:MAX_SHOWN]


def _short(facts: list[str], n: int, names: list[str] = (), packs: bool = True) -> list[str]:
    """Facts that fit a chip: numbers first ("40h battery" beats "stylish"), sizes last (or never, packs=False),
    and never one that only restates a product's name ("Hydrating serum" on the Hydrating Serum)."""
    named = [set(re.findall(r"[a-z]+", n.lower())) for n in names]
    out, seen = [], set()
    for f in sorted((" ".join(f.split()).strip(" .") for f in facts),
                    key=lambda f: (bool(_PACK.search(f)), not re.search(r"\d", f))):
        words = set(re.findall(r"[a-z]+", f.lower()))
        if (not packs and _PACK.search(f)) or any(words and words <= w for w in named):
            continue
        if f and len(f) <= 26 and f.lower() not in seen:
            seen.add(f.lower())
            out.append(f)
    return out[:n]


def price(brief: ClientBrief, value: float) -> str:
    return f"{SYMBOL.get(brief.budget.currency, brief.budget.currency + ' ')}{value:,.0f}"


class _Tags(BaseModel):
    tags: list[str] = []


TAGS_SYSTEM = """Write up to 3 benefit tags for an ad showing a brand's whole range. Each tag at most 24 characters
and true for the range as a whole: a shared benefit, material or claim, or a span across products ("Up to 52h
playtime", "Dermat tested", "Made with merino wool"). Use only the facts given. Never a usage instruction, a size or
pack count, one product's name, or a claim only one product makes."""
_TAG_CACHE: dict[str, list[str]] = {}


def range_facts(products: list[Product], n: int) -> list[str]:
    """Range-level benefit tags written once per range by the model; the shortest per-product facts without it."""
    key = repr([(p.name, p.usp) for p in products])
    if key not in _TAG_CACHE:
        try:
            listing = "\n".join(f"- {p.name}: {'; '.join(p.usp)}" for p in products if p.usp)
            got = llm.structured("fast", TAGS_SYSTEM, f"Products and their facts:\n{listing}", _Tags).tags
            _TAG_CACHE[key] = [t.strip() for t in got if 0 < len(t.strip()) <= 26 and not _PACK.search(t)]
        except Exception:
            _TAG_CACHE[key] = _short([u for p in products for u in p.usp], n, [p.name for p in products], packs=False)
    return _TAG_CACHE[key][:n]


def tags(brief: ClientBrief, products: list[Product], n: int = 3) -> list[str]:
    """Benefit chips for a line-up: what's true across the range, its entry price, its breadth."""
    prices = [x for p in products if (x := p.price_in(brief.budget.currency))]
    extra = ([f"From {price(brief, min(prices))}"] if prices else []) + (
        [f"{len(products)} to choose from"] if len(products) >= 3 else [])
    facts = range_facts(products, n)
    out = (facts[:max(1, n - len(extra))] + extra + facts[max(1, n - len(extra)):])[:n]
    return [t for t in out if not policy_issues(t, brief)]


def card_tags(brief: ClientBrief, p: Product) -> list[str]:
    return [t for t in _short(p.usp, 2, [p.name]) if not policy_issues(t, brief)]
