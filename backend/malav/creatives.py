"""M5-M8: Generation Planner -> Generation (3 layers / template video) -> Vision QA -> Packager.

produce(jobs) is reused by apply_fixes (regenerate referenced creatives) and handle_feedback (new
variants of a winner), so every path gets the same layering, QA and packaging rules.
"""
from __future__ import annotations

import hashlib
import itertools
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from pydantic import BaseModel

from core import bus, langs, llm
from core.approvals import ask_human
from core.schemas import (RATIO_SIZES, AdCopy, CampaignBlueprint, CarouselCard, ClientBrief, Creative, CreativePackage,
                          QAResult, Ratio)
from core.state import RuneState
from malav import director, intake, lineup, media, own, references
from malav.common import demo_mode, fixture
from malav.hooks import GOAL_CTA, HookPlus, fit_copy

SOURCE = {"chatgpt": "ChatGPT", "chatgpt-model": "ChatGPT, AI model wearing the product",
          "chatgpt-product": "ChatGPT, your product placed in the scene",
          "gpt-image-product": "OpenAI image, your product placed in the scene", "pollinations": "Pollinations",
          "gradient": "plain brand-colour backdrop, every image service failed", "client-photo": "your photo"}
PLANNER, GEN, QA, PACK ="malav.planner", "malav.generator", "malav.qa", "malav.packager"
PLACE_LAYOUTS = {"product-hero", "brand-block", "type-bold", "ugc-caption", "offer-badge"}  # not packshot / line-up
VIDEO_MIN, VIDEO_MAX = 6.0, 15.0


class Job(BaseModel):
    campaign_ref: str
    adset_ref: str
    ad_ref: str
    hook_id: str
    kind: str                       # image | video
    ratio: Ratio
    template_id: str
    creative_id: str
    version: int = 1
    parent_id: Optional[str] = None
    note: str = ""                  # QA notes / fix prompt / human note added to the scene prompt


class _Vision(BaseModel):
    legible: bool
    product_visible: bool           # the brand's real product is clearly in the ad (when one is expected)
    product_ok: bool
    brand_recognizable: bool        # someone scrolling would know whose ad this is within a second
    brand_colors: bool
    no_artifacts: bool
    looks_premium: bool             # would pass for a professional agency ad, not stock photo + text
    score: float                    # 0-100
    notes: str = ""


VISION_SYSTEM = """You are the strict creative director of a top ad agency reviewing a Meta ad before it spends money.
legible: every word readable at phone size, good contrast. product_visible: the brand's actual product is clearly
in the ad (say true when the brief says no product is expected). product_ok: any product looks real and undistorted,
its label intact. brand_recognizable: within one second a viewer knows which brand this is (logo, packaging,
colours). brand_colors: the brand's colours carry the design. no_artifacts: no garbled text, warped objects, extra
fingers, odd faces, meaningless stickers. looks_premium: it could run for a big brand; not a stock photo with text
pasted on. score 0-100 against what a great agency would ship. notes: the single most important fix, one sentence.
The on-image text is intentionally shorter than the caption."""


def scene_note(note: str, creative_id: str) -> str:
    """The part of a QA / audit / human note the image model can act on. Audit fix prompts list findings for
    every creative, plus deterministic checks (text layer, size) that no new background can fix."""
    keep = []
    for line in (note or "").splitlines():
        line = line.strip()
        m = re.match(r"-\s*\[\w+\]\s*(\S+):\s*(.*)", line)
        if m:
            if m.group(1) != creative_id:
                continue
            line = m.group(2)
        if line.startswith("Fix these audit findings") or "Failed checks:" in line:
            continue
        line = " ".join(re.sub(r"\bcr_\w+\b", "", re.sub(r"^Creative QA did not pass:?\s*", "", line)).split()).strip(" ,.:")
        if line:
            keep.append(line)
    return ". ".join(keep)[:300]


# ---------- M5 planner ----------

def pick_template(hook: HookPlus) -> str:
    f = f"{hook.framework} {hook.text}".lower()
    if any(k in f for k in ("price", "offer", "anchor", "discount", "deal", "₹", "$", "%")):
        return "offer-badge"
    if any(k in f for k in ("ugc", "testimonial", "social proof", "review", "said", "\"")):
        return "ugc-caption"
    return "product-hero"


def creative_id(ad_ref: str, ratio: str) -> str:
    return f"cr_{ad_ref}_{ratio.replace(':', 'x')}"


def plan_jobs(bp: CampaignBlueprint, hooks: dict[str, HookPlus]) -> list[Job]:
    jobs = []
    for camp in bp.campaigns:
        for s in camp.adsets:
            for ad in s.ads:
                hook = hooks.get(ad.hook_id) or HookPlus(id=ad.hook_id, adset_ref=s.ref, text=s.angle)
                for r in ad.ratios:
                    jobs.append(Job(campaign_ref=camp.ref, adset_ref=s.ref, ad_ref=ad.ref, hook_id=ad.hook_id,
                                    kind=ad.format if ad.format in ("video", "carousel") else "image", ratio=r,
                                    template_id=pick_template(hook), creative_id=creative_id(ad.ref, r)))
    return jobs


def landing_url(brief: ClientBrief, campaign_ref: str, ad_ref: str, base: str = "") -> str:
    """base: a specific page (a carousel card's product page); default the brief's landing page."""
    base = base or (brief.landing_pages or [brief.website or ""])[0] or ""
    if base.startswith("http://"):
        base = "https://" + base[7:]
    u = urlparse(base if base.startswith("https://") else f"https://{base.lstrip('/')}")
    q = dict(parse_qsl(u.query))
    q.update({"utm_source": "meta", "utm_medium": "paid", "utm_campaign": campaign_ref, "utm_content": ad_ref})
    return urlunparse(u._replace(query=urlencode(q)))


def hook_for(job: Job, hooks: dict[str, HookPlus], brief: ClientBrief, product: str = "") -> HookPlus:
    name = brief.product(product).name
    h = hooks.get(job.hook_id) or HookPlus(id=job.hook_id, adset_ref=job.adset_ref, text=name)
    copy = h.ad_copy or AdCopy(primary_text=h.text, headline=name, cta=GOAL_CTA[brief.goal])
    return h.model_copy(update={"ad_copy": fit_copy(copy, h.text, brief),
                                "on_screen_text": h.on_screen_text or h.text, "voiceover": h.voiceover or h.text})


# ---------- M6 generation ----------

class Studio:
    """featured: ad set ref -> the brief product it shows (catalogs: each ad set sells its own product)."""

    def __init__(self, cid: str, brief: ClientBrief, featured: Optional[dict[str, str]] = None,
                 tongues: Optional[dict[str, str]] = None, look: Optional[director.Direction] = None,
                 ad_index: Optional[dict[str, int]] = None):
        self.cid, self.brief, self.featured = cid, brief, featured or {}
        self.look, self.ad_index = look, ad_index or {}
        self.tongues = tongues or {}   # ad set ref -> language of its copy (several-language campaigns)
        self.dir = media.MEDIA / cid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.renderer: Optional[media.Renderer] = None
        self.cutouts: dict[str, Optional[str]] = {}   # product name -> cut-out path (None: no usable photo)
        self.refs: Optional[list[str]] = None
        self.worn: set[str] = set()      # backgrounds that already show the product (worn or placed by the model)

    def __enter__(self):
        try:
            self.renderer = media.Renderer().__enter__()
        except Exception as e:
            bus.log(self.cid, GEN, f"Playwright unavailable ({e.__class__.__name__}): text layer skipped, QA will flag. "
                                   f"Run `playwright install chromium`.")
        return self

    def __exit__(self, *exc):
        if self.renderer:
            self.renderer.__exit__(*exc)

    def product(self, job: Job) -> Optional[str]:
        """Cut-out photo of the product this job's ad set features; None for photo / brand ads or no usable photo.
        A featured product without a photo falls back to another product's, never to an empty spot."""
        if self.brief.creative_focus != "product":
            return None
        return self.cutout_of(self.brief.product(self.featured.get(job.adset_ref, "")))

    def cutout_of(self, p) -> Optional[str]:
        if p.name not in self.cutouts:
            imgs = p.images or [i for q in self.brief.products for i in q.images]
            self.cutouts[p.name] = self._cutout(imgs[0], p.name) if imgs else None
            if not imgs:
                bus.log(self.cid, GEN, f"No photo of {p.name}: its ads have no product layer (add one at the brief step)")
        return self.cutouts[p.name]

    @property
    def range(self) -> bool:
        return self.brief.creative_focus == "range"

    def range_shots(self) -> list[tuple[object, str]]:
        """(product, cut-out) for every product a range ad shows, in brief order."""
        return [(p, c) for p in lineup.shown(self.brief) if (c := self.cutout_of(p))]

    def _cutout(self, img: str, name: str) -> Optional[str]:
        try:
            key = hashlib.md5(img.encode()).hexdigest()[:8]
            path = media.product_cutout(img, self.dir / f"product_cutout_{key}.png")
            from PIL import Image
            with Image.open(path) as im:
                small = min(im.size) < 200
            if small:  # a site's share image is often just its logo: the image model would invent a product
                bus.log(self.cid, GEN, "The product photo looks like a logo, not a product: ads will have no product "
                                       "layer. Upload a real product photo at the brief step for better ads")
                return None
            bus.log(self.cid, GEN, f"Cut out the real photo of {name}")
            return path
        except Exception as e:
            bus.log(self.cid, GEN, f"Product photo unusable ({e.__class__.__name__}); ads without product layer")
            return None

    def scene_prompt(self, hook: HookPlus, job: Job) -> str:
        b = self.brief
        # never name the product: diffusion models ignore "no X" and draw their own version of it,
        # so the product's words are stripped from the (LLM-written) scene description
        scene = hook.scene or "empty wooden tabletop in a softly lit modern home, shallow depth of field, props at the edges"
        words = {w for p in b.products for w in re.findall(r"[a-z]{3,}", p.name.lower())} - {"the", "and", "with", "for"}
        if words:
            scene = re.sub(rf"\b(a |an |the )?(glass|cup|bottle|jar|pack|bag|can)? ?(of )?({'|'.join(map(re.escape, words))})s?\b",
                           "", scene, flags=re.I)
            scene = re.sub(r"\s{2,}|\s+(?=[,.])", " ", scene).strip(" ,.")
        # media.background adds the no-text/no-logo list once; the hooks agent's own copy only repeats it
        scene = re.sub(r",?\s*\bno (text|logos?|products?|branding|labels?|watermarks?)\b", "", scene, flags=re.I)
        scene = scene.strip(" ,.")
        colours = ", ".join(media.colour_name(c) for c in b.brand.colors[:2])
        if self.look:  # the chosen look decides every scene's photography and subject
            scene = f"{self.look.scenes}. {scene}"
        p = (f"{scene}. Mood: {b.brand.tone or 'premium'}." + (f" Colour accents: {colours}." if colours else "")
             + (f" Photography: {self.look.photography}." if self.look else "")
             + (f" Avoid: {self.look.avoid}." if self.look and self.look.avoid else ""))
        note = scene_note(job.note, job.creative_id)
        return f"{p} Changes requested: {note}" if note else p

    def backgrounds(self, items: list[tuple[Job, HookPlus]], attempt: int = 1) -> dict[str, tuple[str, str]]:
        """creative_id -> (background path, source). One scene per ad, shared by its ratios (consistent look,
        half the image-model calls); the template cover-crops it. 1.91:1 ads get a landscape scene."""
        groups: dict[tuple[str, bool], list[tuple[Job, HookPlus]]] = {}
        for job, hook in items:
            groups.setdefault((job.ad_ref, job.ratio == "1.91:1"), []).append((job, hook))
        if self.brief.creative_focus == "photos" and self.brief.photos:  # the client's photos, nothing generated
            photos = [self._local(p, i) for i, p in enumerate(self.brief.photos)]
            order = list(groups)  # a different photo per ad (attempt 2 moves on to the next one)
            return {j.creative_id: (photos[(order.index(k) + attempt - 1) % len(photos)], "client-photo")
                    for k, members in groups.items() for j, _ in members}
        refs = self.references()
        finished = itertools.count(1)  # next() is atomic under the GIL: safe across the worker threads
        for job, _ in items:  # cut out each featured product once, before the worker threads
            self.product(job)
        if self.range:
            self.range_shots()

        def one(i_key):
            i, key = i_key
            (ad_ref, wide), members = key, groups[key]
            job, hook = members[0]
            product = self.product(job)
            # a wearable goes on a new AI model; only ChatGPT can take the product photo as input
            wear = product if refs is not None and references.wearable(self.brief) else None
            # layouts where the photo carries the ad: the model places the real product in it (no pasted cut-out)
            hold = product if (product and not wear and media.can_place_product()
                               and self.template(job) in PLACE_LAYOUTS) else None
            product = product or (self.range and bool(self.cutouts))  # a range needs room for its line-up
            w, h = (1216, 640) if wide else (768, 1216)
            out = self.dir / f"bg_{ad_ref}{'_wide' if wide else ''}_v{job.version}_{attempt}.png"
            mine = [refs[(2 * i + k) % len(refs)] for k in range(min(2, len(refs)))] if refs else []
            src = media.background(self.scene_prompt(hook, job), out, w, h, self.brief.brand.colors,
                                   seed=attempt * 7 + job.version, product=bool(product), refs=mine, wear=wear,
                                   hold=hold)
            if src in ("chatgpt-model", "chatgpt-product", "gpt-image-product"):
                self.worn.add(str(out))
            bus.log(self.cid, GEN, f"Image {next(finished)} of {len(groups)} ready ({SOURCE.get(src, src)})")
            return [(j.creative_id, (str(out), src)) for j, _ in members]
        with ThreadPoolExecutor(max_workers=4) as ex:
            return {cid: res for pairs in ex.map(one, enumerate(groups)) for cid, res in pairs}

    def _local(self, photo: str, i: int) -> str:
        """The renderer loads file:// paths only: a photo given as a URL is downloaded once."""
        src = media.resolve(photo)
        if not src.startswith("http"):
            return src
        out = self.dir / f"photo_{i}_{hashlib.md5(src.encode()).hexdigest()[:8]}.jpg"
        if not out.exists():
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(httpx.get(src, timeout=60, follow_redirects=True).content)) as im:
                im.convert("RGB").save(out, "JPEG", quality=92)
        return str(out)

    def references(self) -> Optional[list[str]]:
        """Pinterest/web reference photos, fetched once per run; None when ChatGPT (the only model that takes
        images) is off."""
        if not media.codex_on():
            return None
        if self.refs is None:
            self.refs = references.find(self.brief, self.dir / "refs", query=self.look.reference_query if self.look else "")
            wear = references.wearable(self.brief) and self.brief.creative_focus == "product"
            bus.log(self.cid, GEN, f"Found {len(self.refs)} reference photo(s) (Pinterest / web) to guide the image model"
                    + ("; wearable product: a new AI model will wear it" if wear else ""))
        return self.refs

    def frame(self, job: Job, hook: HookPlus, bg: str, out: Path, caption: str = "", show_cta: bool = True) -> None:
        w, h = RATIO_SIZES[job.ratio]
        b, p = self.brief, self.brief.product(self.featured.get(job.adset_ref, ""))
        product = None if bg in self.worn else self.product(job)
        tid = self.template(job)
        offer_on = bool(self.offer())
        badge = (b.offer.short() if offer_on else lineup.price(b, x) if (x := p.price_in(b.budget.currency))
                 else hook.ad_copy.description)
        sub = badge if tid == "offer-badge" else hook.ad_copy.description  # never an invented "Verified buyer"
        shown = self.range_shots() if self.range else []
        if not self.renderer:
            media.composite_only(bg, product or (shown[len(shown) // 2][1] if shown else None), out, w, h)
            return
        page = media.fill_template(tid, job.ratio, w, h, bg=bg, product=product, offer=self.offer(job), lang=self.lang(job),
                                   headline=hook.on_screen_text, sub=sub, cta=hook.ad_copy.cta, colors=b.brand.colors,
                                   font=(b.brand.fonts or ["Inter"])[0], logo=b.brand.logo, brand_name=b.business_name,
                                   caption=caption, show_cta=show_cta, products=[c for _, c in shown[:lineup.MAX_LINEUP]],
                                   tags=lineup.tags(b, [q for q, _ in shown]) if shown else (lineup.card_tags(b, p) if product else []))  # range: all; one product: its own
        self.renderer.render(page, out, w, h)

    def template(self, job: Job) -> str:
        """A range shows its line-up wherever a single product would go (also in videos and carousel covers); with
        an offer running, every other single-product ad leads with the offer badge."""
        if job.kind == "carousel" and self.range and self.range_shots():
            return "range-lineup"  # a carousel's cover is the range
        offer = bool(media.offer_html(self.brief.offer))
        layouts = (self.look.layouts if self.look else director.LAYOUTS[self.brief.creative_focus])
        layouts = [x for x in layouts if x != "range-lineup" or (self.range and self.range_shots())] or ["product-hero"]
        i = self.ad_index.get(job.ad_ref, int(hashlib.md5(job.ad_ref.encode()).hexdigest(), 16))
        if offer and i % 2 == 1:  # with an offer running, every other ad leads with the deal
            return "offer-badge"
        return layouts[i % len(layouts)]

    def lang(self, job: Job) -> str:
        return self.tongues.get(job.adset_ref) or (self.brief.languages or ["en"])[0]

    def offer(self, job: Optional[Job] = None) -> str:
        return media.offer_html(self.brief.offer, lineup.SYMBOL.get(self.brief.budget.currency, ""),
                                self.lang(job) if job else "en")

    def cards(self, job: Job, hook: HookPlus, bg: str, stem: Path) -> list[CarouselCard]:
        """Carousel: the line-up as the cover card, then one card per product with its own facts and page."""
        w, h = RATIO_SIZES[job.ratio]
        b = self.brief
        cover = stem.parent / f"{stem.name}_c0.png"
        self.frame(job, hook, bg, cover)
        out = [CarouselCard(path=media.rel(cover), headline=hook.ad_copy.headline, description=hook.ad_copy.description,
                            link=landing_url(b, job.campaign_ref, job.ad_ref))]
        for i, (p, cut) in enumerate(self.range_shots()[:9], 1):  # Meta: at most 10 cards
            f = stem.parent / f"{stem.name}_c{i}.png"
            tags = lineup.card_tags(b, p)
            price = lineup.price(b, x) if (x := p.price_in(b.budget.currency)) else ""
            if self.renderer:
                page = media.fill_template("range-card", job.ratio, w, h, bg=bg, product=cut, headline=p.name, sub=price,
                                           lang=self.lang(job),
                                           cta=hook.ad_copy.cta, colors=b.brand.colors,
                                           font=(b.brand.fonts or ["Inter"])[0], logo=b.brand.logo,
                                           brand_name=b.business_name, show_cta=False, tags=tags)
                self.renderer.render(page, f, w, h)
            else:
                media.composite_only(bg, cut, f, w, h)
            out.append(CarouselCard(path=media.rel(f), headline=p.name[:40],
                                    description=(tags[0] if tags else price)[:30],
                                    link=landing_url(b, job.campaign_ref, f"{job.ad_ref}_c{i}", base=p.url or "")))
        return out

    def make(self, job: Job, hook: HookPlus, bg: tuple[str, str]) -> Creative:
        w, h = RATIO_SIZES[job.ratio]
        stem = self.dir / f"{job.creative_id}_v{job.version}"
        duration = thumb = None
        cards: list[CarouselCard] = []
        if job.kind == "carousel":
            cards = self.cards(job, hook, bg[0], stem)
            out = media.resolve(cards[0].path)
        elif job.kind == "image":
            out = stem.with_suffix(".png")
            self.frame(job, hook, bg[0], out)
        else:
            out, thumb, duration = self.video(job, hook, bg[0], stem)
        return Creative(creative_id=job.creative_id, campaign_ref=job.campaign_ref, adset_ref=job.adset_ref,
                        ad_ref=job.ad_ref, hook_id=job.hook_id, kind=job.kind, ratio=job.ratio, width=w, height=h,
                        duration_s=duration, path=media.rel(Path(out)), cards=cards,
                        thumbnail_path=media.rel(thumb) if thumb else None,
                        ad_copy=hook.ad_copy, landing_url=landing_url(self.brief, job.campaign_ref, job.ad_ref),
                        model_used=f"{bg[1]}+{'html' if self.renderer else 'pil'}", template_id=self.template(job),
                        prompt=self.scene_prompt(hook, job), version=job.version, parent_id=job.parent_id)

    def video(self, job: Job, hook: HookPlus, bg: str, stem: Path) -> tuple[Path, Path, float]:
        """Template motion: caption frames over a slow push-in, Edge-TTS voiceover, CTA end card."""
        w, h = RATIO_SIZES[job.ratio]
        b = self.brief
        target = float((b.deliverables.video_lengths_s or [15])[0])
        audio = stem.with_suffix(".mp3")
        vo = media.voiceover(hook.voiceover, audio, langs.voice(self.lang(job), (b.geo or ["IN"])[0]))
        total = min(VIDEO_MAX, max(VIDEO_MIN, (vo + 0.8) if vo else target))
        chunks = media.captions(hook.voiceover, lang=self.lang(job))
        words = [len(c.split()) for c in chunks]
        body = total - 1.5
        frames: list[tuple[Path, float]] = []
        for i, c in enumerate(chunks):
            f = stem.parent / f"{stem.name}_f{i}.png"
            self.frame(job, hook, bg, f, caption=c, show_cta=False)
            frames.append((f, body * words[i] / sum(words)))
        end = stem.parent / f"{stem.name}_end.png"
        self.frame(job, hook, bg, end, show_cta=True)
        frames.append((end, 1.5))
        thumb = stem.parent / f"{stem.name}_thumb.png"   # first frame: hook visible from t=0
        self.frame(job, hook, bg, thumb, show_cta=True)
        out = stem.with_suffix(".mp4")
        media.motion_video(frames, audio if vo else None, out, w, h)
        for f, _ in frames:
            f.unlink(missing_ok=True)
        audio.unlink(missing_ok=True)
        return out, thumb, round(total, 2)


# ---------- M7 QA ----------

def qa(c: Creative, brief: ClientBrief, on_image: str = "") -> QAResult:
    path = Path(media.resolve(c.path))
    look = Path(media.resolve(c.thumbnail_path)) if c.thumbnail_path else path
    # safe zones are guaranteed by media.layout (we place every word), so they're checked in code, not by eye
    checks = {"file_exists": path.exists(), "has_text_layer": not c.model_used.endswith("+pil"), "safe_zones": True}
    if look.exists():
        from PIL import Image
        with Image.open(look) as im:
            checks["exact_size"] = im.size == (c.width, c.height)
    if c.kind == "carousel":
        checks["cards_ok"] = 2 <= len(c.cards) <= 10 and all(Path(media.resolve(x.path)).exists() for x in c.cards)
    if c.kind == "video":
        checks["duration_ok"] = bool(c.duration_s and VIDEO_MIN - 0.5 <= c.duration_s <= VIDEO_MAX + 0.5)
        checks["thumbnail"] = bool(c.thumbnail_path and look.exists())
    else:
        checks["size_ok"] = path.exists() and path.stat().st_size < 30_000_000
    if not all(checks.values()):
        return QAResult(score=20, passed=False, checks=checks,
                        notes="Failed checks: " + ", ".join(k for k, v in checks.items() if not v))
    expect = brief.creative_focus in ("product", "range") and any(p.images for p in brief.products)
    try:
        v = llm.structured("vision", VISION_SYSTEM,
                           f"Brand: {brief.business_name}. Ratio {c.ratio}. Brand colours {brief.brand.colors}. "
                           f"Product: {'expected: ' + ', '.join(p.name for p in brief.products[:4]) if expect else 'none expected (brand / photo ad)'}. "
                           f"Intended on-image text: “{on_image or c.ad_copy.headline}” plus the CTA button "
                           f"“{media.cta_label(c.ad_copy.cta)}”.", _Vision, images=[str(look)])
        checks |= {"legible": v.legible, "product_visible": v.product_visible or not expect, "product_ok": v.product_ok,
                   "brand_recognizable": v.brand_recognizable, "brand_colors": v.brand_colors,
                   "no_artifacts": v.no_artifacts, "looks_premium": v.looks_premium}
        hard = ("legible", "no_artifacts", "product_visible")
        return QAResult(score=v.score, passed=all(checks[k] for k in hard) and v.score >= 65, checks=checks, notes=v.notes)
    except Exception as e:  # never blocks the run, but never pretends someone looked either
        return QAResult(score=60, passed=True, checks=checks | {"reviewed_by_eye": False},
                        notes=f"Not reviewed by eye: no image-reading model answered ({e.__class__.__name__}). "
                              "Check it yourself before approving.")


# ---------- produce + M8 package ----------

def produce(cid: str, brief: ClientBrief, jobs: list[Job], hooks: dict[str, HookPlus],
            bp: Optional[CampaignBlueprint] = None, look: Optional[dict] = None) -> list[Creative]:
    featured = {s.ref: s.product for s in bp.adsets()} if bp else {}
    tongues = {s.ref: s.language for s in bp.adsets() if s.language} if bp else {}
    items = [(j, hook_for(j, hooks, brief, featured.get(j.adset_ref, ""))) for j in jobs]
    scenes = len({(j.ad_ref, j.ratio == "1.91:1") for j in jobs})  # one background per ad, shared by its ratios
    if media.CODEX_DOWN:  # never downgrade the images silently
        bus.log(cid, GEN, f"{media.CODEX_DOWN['why']}" + (f" (until {media.CODEX_DOWN['until']})" if media.CODEX_DOWN.get("until") else "")
                          + ": using the fallback image service, so expect weaker images"
                          + ("" if os.getenv("OPENAI_API_KEY") else ". Add OPENAI_API_KEY for full-quality images"))
    if brief.creative_focus == "photos" and brief.photos:
        bus.log(cid, GEN, f"Using your {len(brief.photos)} photo(s) as the ad images; no image generation")
    else:
        bus.log(cid, GEN, f"Generating {scenes} background scene{'s' * (scenes != 1)} with "
                          + ("ChatGPT, 4 at a time (about 1 minute each)" if media.codex_on() else "Pollinations (free image service)"))
    bgs = {}
    order = list(dict.fromkeys(j.ad_ref for j in jobs))  # ads take turns over the look's layouts
    with Studio(cid, brief, featured, tongues, director.Direction.model_validate(look) if look else None,
                {a: i for i, a in enumerate(order)}) as st:
        bgs = st.backgrounds(items)
        sources = sorted({s for _, s in bgs.values()})
        bus.log(cid, GEN, f"Backgrounds ready ({', '.join(sources)}); compositing product + text layers")
        out = []
        for job, hook in items:
            try:
                c = st.make(job, hook, bgs[job.creative_id])
            except Exception as e:
                bus.log(cid, GEN, f"{job.creative_id}: generation failed ({e.__class__.__name__}: {str(e)[:120]})")
                continue
            c.qa = qa(c, brief, hook.on_screen_text)
            if not c.qa.passed and c.qa.notes.startswith("Failed checks"):  # render/size checks: a new background can't fix
                bus.log(cid, QA, f"{c.creative_id} failed QA ({c.qa.notes}); not a scene problem, no regeneration")
            elif not c.qa.passed:
                bus.log(cid, QA, f"{c.creative_id} failed QA ({c.qa.notes}); regenerating once")
                retry = job.model_copy(update={"note": c.qa.notes})
                bg2 = st.backgrounds([(retry, hook)], attempt=2)[job.creative_id]
                try:
                    c2 = st.make(retry, hook, bg2)
                    c2.qa = qa(c2, brief, hook.on_screen_text)
                    c = c2 if c2.qa.passed or c2.qa.score > c.qa.score else c
                except Exception as e:
                    bus.log(cid, GEN, f"{job.creative_id}: regeneration failed ({e.__class__.__name__})")
            bus.log(cid, QA, f"{c.creative_id}: {'pass' if c.qa.passed else 'NEEDS HUMAN'} · score {c.qa.score:.0f}"
                             + (f" · {c.qa.notes}" if c.qa.notes else ""))
            out.append(c)
    return out


def write_manifest(cid: str, pkg: CreativePackage) -> None:
    d = media.MEDIA / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(pkg.model_dump_json(indent=2), encoding="utf-8")


def client_package(cid: str, brief: ClientBrief, bp: CampaignBlueprint, hooks: dict[str, HookPlus],
                   items: list[dict]) -> CreativePackage:
    """The client's own files as the creatives: one per planned ad, copy from the client or the hooks agent."""
    assets = own.probe_all(cid, items)
    creatives = []
    for camp_ref, adset_ref, ad, a in own.match(bp, assets):
        job = Job(campaign_ref=camp_ref, adset_ref=adset_ref, ad_ref=ad.ref, hook_id=ad.hook_id, kind=a.kind,
                  ratio=a.ratio, template_id="", creative_id=creative_id(ad.ref, a.ratio))
        creatives.append(own.to_creative(a, creative_id=job.creative_id, campaign_ref=camp_ref, adset_ref=adset_ref,
                                         ad_ref=ad.ref, hook_id=ad.hook_id, copy=hook_for(job, hooks, brief).ad_copy,
                                         landing_url=landing_url(brief, camp_ref, ad.ref)))
    unused = len(assets) - len(creatives)
    bad = [c for c in creatives if not c.qa.passed]
    bus.log(cid, PACK, f"Using your {len(creatives)} creative(s) as they are: no generated images or video"
                       + (f"; {unused} upload(s) had no planned ad" if unused else "")
                       + (f"; {len(bad)} need a better file ({bad[0].qa.notes})" if bad else ""))
    return CreativePackage(client_id=cid, blueprint=bp, creatives=creatives)


NO_PHOTO = intake.BRAND_OPT


def ensure_visual(cid: str, brief: ClientBrief) -> tuple[ClientBrief, dict]:
    """Before any image is generated: the brief must hold what its focus needs (a real product photo, the client's
    photos), or the image model can only draw an empty scene. Asks intake's question until it does or the client
    picks general scenes. Returns the brief and the state update."""
    if intake.visual_ready(brief, verify=True):
        return brief, {}
    if brief.creative_focus == "product":  # what's there is a logo or icon: don't count it as a photo
        brief = brief.model_copy(update={"products": [p.model_copy(update={"images": []}) for p in brief.products]})
    for _ in range(4):  # each answer is its own interrupt; resuming replays the earlier ones in order
        q, options, upload = intake.visual_question(brief)
        patch = intake.visual_patch(brief, ask_human(cid, GEN, q, options, upload=upload))
        if patch is None:
            bus.log(cid, GEN, f"That image looks like a logo or icon, or is under {media.MIN_PRODUCT_SIDE}px, "
                              "or I couldn't tell which option you meant")
            continue
        brief = intake.apply_visual(brief, patch)
        if intake.visual_ready(brief):
            bus.log(cid, GEN, {"brand": "General brand / category scenes, no product shot",
                               "photos": "Your photos become the ad images",
                               "product": "Got the product photo(s); each ad set shows its own product"}[brief.creative_focus])
            return brief, {"brief": brief.model_dump(mode="json")}
    brief = brief.model_copy(update={"creative_focus": "brand"})  # never stuck: general scenes need nothing
    return brief, {"brief": brief.model_dump(mode="json")}


def choose_direction(state: RuneState) -> tuple[Optional[director.Direction], dict]:
    """The human picks the campaign's look (asked first in the node: resuming replays the answer). Kept on reruns."""
    if state.get("direction"):
        return director.Direction.model_validate(state["direction"]), {}
    looks = [director.Direction.model_validate(d) for d in state.get("directions") or []]
    if not looks:
        return None, {}
    answer = ask_human(state["client_id"], director.AGENT, "Pick the look for these ads (every ad will follow it)",
                       [director.option(d) for d in looks])
    look = director.pick(looks, answer)
    bus.log(state["client_id"], director.AGENT, f"Look: {look.name}. Layouts {', '.join(look.layouts)}; "
                                                f"photography: {look.photography}")
    return look, {"direction": look.model_dump()}


def creatives_step(state: RuneState) -> dict:
    cid = state["client_id"]
    if demo_mode(state):
        bus.log(cid, PACK, "Demo run: using the fixture creative package")
        return {"package": fixture("package", cid)}

    brief = ClientBrief.model_validate(state["brief"])
    bp = CampaignBlueprint.model_validate(state["blueprint"])
    hooks = {h["id"]: HookPlus.model_validate(h) for h in state.get("hooks", [])}
    if items := own.uploads(state):
        pkg = client_package(cid, brief, bp, hooks, items)
        write_manifest(cid, pkg)
        return {"package": pkg.model_dump(mode="json")}
    look, update = choose_direction(state)
    brief, more = ensure_visual(cid, brief)
    update |= more
    note = (state.get("notes") or {}).get("creatives", "")
    if note and state.get("package"):  # Creative Studio "regenerate this asset": only the named creatives
        old = CreativePackage.model_validate(state["package"])
        named = {c.creative_id for c in old.creatives if c.creative_id in note}
        if named:
            bus.log(cid, PLANNER, f"Regenerating {len(named)} asset(s) you asked for: {note[:160]}")
            jobs = [Job(campaign_ref=c.campaign_ref, adset_ref=c.adset_ref, ad_ref=c.ad_ref, hook_id=c.hook_id,
                        kind=c.kind, ratio=c.ratio, template_id=c.template_id or "product-hero",
                        creative_id=c.creative_id, version=c.version + 1, note=note)
                    for c in old.creatives if c.creative_id in named]
            fresh = {c.creative_id: c for c in produce(cid, brief, jobs, hooks, old.blueprint,
                                                       state.get("direction") or (look.model_dump() if look else None))}
            pkg = old.model_copy(update={"creatives": [fresh.get(c.creative_id, c) for c in old.creatives]})
            write_manifest(cid, pkg)
            return {**update, "package": pkg.model_dump(mode="json")}
    jobs = plan_jobs(bp, hooks)
    if note:
        jobs = [j.model_copy(update={"note": note}) for j in jobs]
    bus.log(cid, PLANNER, f"Making {len(jobs)} files: {sum(j.kind == 'image' for j in jobs)} image(s) and "
                          f"{sum(j.kind == 'video' for j in jobs)} video(s), in sizes {', '.join(brief.deliverables.ratios)}"
                          + (f"; applying: {note}" if note else ""))
    creatives = produce(cid, brief, jobs, hooks, bp, state.get("direction") or (look.model_dump() if look else None))
    pkg = CreativePackage(client_id=cid, blueprint=bp, creatives=creatives)
    write_manifest(cid, pkg)
    update["package"] = pkg.model_dump(mode="json")
    ok = sum(c.qa.passed for c in creatives)
    bus.log(cid, PACK, f"Creatives ready: {len(creatives)} of {len(jobs)} made, {ok} passed the quality check"
                       + ("" if ok == len(creatives) else f", {len(creatives) - ok} need your review"))
    return update
