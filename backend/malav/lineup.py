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


# words in a client's note that say what they want done, not what they sell
_NOTE_WORDS = {"range", "product", "campaign", "general", "brand", "showcase", "about", "their", "with", "collection",
               "raise", "awareness", "presence", "scene", "want", "wants", "promote", "launch", "market", "marketing",
               "sale", "sales", "advert", "advertise", "advertising", "people", "customer", "customers", "more",
               "new", "every", "first", "audience", "reach", "grow", "growth", "boost", "awareness", "visibility",
               "category", "space", "world", "india", "online", "store", "shop", "buy", "make", "should", "would"}


def _words(hint: str, url: str) -> set[str]:
    """What the range is about, stemmed. The brand's own name is dropped: it's in every link on its site."""
    return ({_stem(w) for w in re.findall(r"[a-z]{4,}", hint.lower())} - _NOTE_WORDS
            - {_stem(w) for w in re.findall(r"[a-z]{4,}", site(url))})


def search_hint(url: str, known: list[Product], note: str, brand: str = "") -> str:
    """What to look for on the site: the words the products' names share, the URL's path ("/protein"), then the
    client's note. Product names say what is sold far better than a note about what the client wants."""
    from collections import Counter
    counts = Counter(t for p in known for t in _tokens(p.name, brand))
    common = [t for t, n in counts.most_common(4) if n >= max(2, len(known) // 3)] or [t for t, _ in counts.most_common(2)]
    path = [w for w in re.findall(r"[a-z]{4,}", urlparse(url).path.lower())]
    return " ".join(dict.fromkeys(path + common)) or note


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


def _tokens(name: str, brand: str = "") -> set[str]:
    drop = set(re.findall(r"[a-z0-9]+", brand.lower())) | {"pack", "of", "the", "and", "with", "g", "ml", "kg", "l"}
    return {t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in drop and not t.isdigit()}


def _distinct(names: list[str], brand: str) -> list[set[str]]:
    """Each name's words minus the ones most names share ("high", "protein" in a protein range): what tells products
    apart is what matching must agree on."""
    sets = [_tokens(n, brand) for n in names]
    from collections import Counter
    df = Counter(t for ts in sets for t in ts)
    common = {t for t, n in df.items() if len(sets) >= 4 and n > 0.4 * len(sets)}
    return [ts - common or ts for ts in sets]


def match(known: list[Product], pool: list[Product], brand: str = "") -> tuple[list[Product], set[int]]:
    """Fill the brief's own products from what the site has: a pool product agreeing on the distinguishing words
    (paneer / dahi / lassi, not "high protein") gives it its photo, page, price and facts. A wrong product photo is
    worse than none, so the bar is high. Returns (products, indexes of the pool products used)."""
    sets = _distinct([k.name for k in known] + [c.name for c in pool], brand)
    ks, cs = sets[:len(known)], sets[len(known):]
    out, used = [], set()
    for k, kt in zip(known, ks):
        best, score = None, 0.0
        for i, (c, ct) in enumerate(zip(pool, cs)):
            if not c.images or not (kt & ct):
                continue
            j = len(kt & ct) / len(kt | ct)
            if j > score:
                best, score = i, j
        if best is not None and score >= 0.5:
            used.add(best)
            if not k.images:
                c = pool[best]
                k = k.model_copy(update={"images": c.images, "url": k.url or c.url, "price": k.price or c.price,
                                         "currency": k.currency or c.currency, "usp": [u for u in c.usp if u] or k.usp})
        out.append(k)
    return out, used


def find_range(cid: str, url: str, hint: str, known: list[Product], max_reads: int = 4, add_new: bool = True,
               brand: str = "") -> list[Product]:
    """The brand's products with real photos where the site has them. Never raises.
    The brief's own products (`known`) come first, filled from the site by name; add_new also adds the other
    products found (a range). Tries the store's own product API first (Shopify / WooCommerce: exact and instant);
    else reads the best listing pages (one level deeper when they list categories) and product pages from search."""
    hint = search_hint(url, known, hint, brand) if (known or urlparse(url or "").path.strip("/")) else hint
    pool: list[Product] = []
    if url and len(feed := [p for p in shop_feed(url, hint) if p.images]) >= 2:
        pool = feed
        bus.log(cid, AGENT, f"Read {len(feed)} products from the store's product catalogue: "
                            + ", ".join(p.name for p in feed[:6]))
    elif url:
        queue, direct = _pages(url, hint)
        read, dom = set(), site(url)
        while queue and len(read) < max_reads and len(pool) < 10:
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
                pool = [p for p in pool if (p.url or "").rstrip("/") != page.rstrip("/")]
            names = {p.name.lower() for p in pool}
            fresh = [p for p in items if p.name.lower() not in names]
            pool += fresh
            if len(fresh) < 3:  # maybe categories: look one level deeper
                queue += [p.url for p in fresh if p.url and urlparse(p.url).netloc.lower().endswith(dom) and p.url not in read]
        names = {p.name.lower() for p in pool}
        direct = [p for p in direct if p.name.lower() not in names][:8]
        with ThreadPoolExecutor(max_workers=4) as ex:  # product pages the search found: their own facts and price
            pool += list(ex.map(lambda p: _details(p, hint), direct))

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
        pool = list(ex.map(photo, pool[:14]))
    found, used = match(known, pool, brand)
    if add_new:
        sets = _distinct([k.name for k in found] + [c.name for c in pool], brand)
        mine, theirs = sets[:len(found)], sets[len(found):]
        extra, seen = [], [*mine]
        for i, (c, ct) in enumerate(zip(pool, theirs)):
            # a known product or a variant already shown (same distinguishing words) isn't a new product
            if i in used or any(ct and ct <= x or x and x <= ct for x in seen):
                continue
            seen.append(ct)
            extra.append(c)
        found += extra[:max(0, 12 - len(found))]
    bus.log(cid, AGENT, f"Products: {len(found)}, {sum(bool(p.images) for p in found)} with a real photo")
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
