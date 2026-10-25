"""Reference images for ad generation: Pinterest pins (then the open web) via Tavily image search.

References only steer pose, framing, lighting and styling. Nobody in them appears in an ad: wearables get a
new AI-generated model wearing the client's real product (media.background with `wear=`).
"""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import httpx

from core.schemas import ClientBrief
from malav import web

WEARABLE = re.compile(r"\b(t-?shirts?|tees?|shirts?|polos?|hoodies?|sweatshirts?|jackets?|dress(es)?|jeans|trousers|"
                      r"pants|shorts|skirts?|kurtas?|sarees?|apparel|clothing|fashion|streetwear|wear|caps?|hats?|"
                      r"sneakers|shoes|footwear|bags?|jewel(le)?ry|watch(es)?|sunglasses)\b", re.I)


def wearable(brief: ClientBrief) -> bool:
    return bool(WEARABLE.search(" ".join([brief.industry, *(p.name for p in brief.products)])))


def find(brief: ClientBrief, out_dir: Path, n: int = 6) -> list[str]:
    """Download up to n reference photos into out_dir; [] when search is unavailable. Never raises."""
    what = (f"{brief.industry} model wearing, editorial fashion photoshoot" if wearable(brief)
            else f"{brief.industry} product photography, styled lifestyle scene")
    urls: list[str] = []
    for q in (f"{what} site:pinterest.com", what):
        try:
            urls += web.image_search(q, n * 2)
        except Exception:
            pass
        if len(urls) >= n:
            break
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    from PIL import Image
    for u in dict.fromkeys(urls):
        p = out_dir / f"ref_{len(saved)}.jpg"
        try:
            r = httpx.get(u, headers=web.UA, timeout=20, follow_redirects=True)
            r.raise_for_status()
            with Image.open(BytesIO(r.content)) as im:
                if min(im.size) < 300:  # thumbnails tell the model nothing
                    continue
                im.convert("RGB").save(p, "JPEG", quality=90)
        except Exception:
            continue
        saved.append(str(p))
        if len(saved) >= n:
            break
    return saved
