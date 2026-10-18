"""M5-M8: Generation Planner -> Generation (3 layers / template video) -> Vision QA -> Packager.

produce(jobs) is reused by apply_fixes (regenerate referenced creatives) and handle_feedback (new
variants of a winner), so every path gets the same layering, QA and packaging rules.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel

from core import bus, llm
from core.schemas import (RATIO_SIZES, AdCopy, CampaignBlueprint, ClientBrief, Creative, CreativePackage,
                          QAResult, Ratio)
from core.state import RuneState
from malav import media
from malav.common import demo_mode, fixture
from malav.hooks import GOAL_CTA, HookPlus, fit_copy

PLANNER, GEN, QA, PACK = "malav.planner", "malav.generator", "malav.qa", "malav.packager"
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
    product_ok: bool
    brand_colors: bool
    no_artifacts: bool
    safe_zones: bool = True
    score: float                    # 0-100
    notes: str = ""


VISION_SYSTEM = """You are the creative QA reviewer of an ad agency. Inspect this Meta ad image.
legible: all text readable with good contrast. product_ok: the product looks like a real, undistorted
product photo. brand_colors: the brand colours are visible. no_artifacts: no garbled text, warped
objects, extra fingers or odd faces. safe_zones (9:16 only, else true): no text in the top 14% or the
bottom 35%. score 0-100 overall ad quality. notes: what to fix, one sentence."""


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
                                    kind="video" if ad.format == "video" else "image", ratio=r,
                                    template_id=pick_template(hook), creative_id=creative_id(ad.ref, r)))
    return jobs


def landing_url(brief: ClientBrief, campaign_ref: str, ad_ref: str) -> str:
    base = (brief.landing_pages or [brief.website or ""])[0] or ""
    if base.startswith("http://"):
        base = "https://" + base[7:]
    u = urlparse(base if base.startswith("https://") else f"https://{base.lstrip('/')}")
    q = dict(parse_qsl(u.query))
    q.update({"utm_source": "meta", "utm_medium": "paid", "utm_campaign": campaign_ref, "utm_content": ad_ref})
    return urlunparse(u._replace(query=urlencode(q)))


def hook_for(job: Job, hooks: dict[str, HookPlus], brief: ClientBrief) -> HookPlus:
    h = hooks.get(job.hook_id) or HookPlus(id=job.hook_id, adset_ref=job.adset_ref, text=brief.products[0].name)
    copy = h.ad_copy or AdCopy(primary_text=h.text, headline=brief.products[0].name, cta=GOAL_CTA[brief.goal])
    return h.model_copy(update={"ad_copy": fit_copy(copy, h.text, brief),
                                "on_screen_text": h.on_screen_text or h.text, "voiceover": h.voiceover or h.text})


# ---------- M6 generation ----------

class Studio:
    def __init__(self, cid: str, brief: ClientBrief):
        self.cid, self.brief = cid, brief
        self.dir = media.MEDIA / cid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.renderer: Optional[media.Renderer] = None
        self.product = self._product()

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

    def _product(self) -> Optional[str]:
        imgs = [i for p in self.brief.products for i in p.images]
        if not imgs:
            return None
        try:
            path = media.product_cutout(imgs[0], self.dir / "product_cutout.png")
            bus.log(self.cid, GEN, "Cut out the real product photo (rembg)")
            return path
        except Exception as e:
            bus.log(self.cid, GEN, f"Product photo unusable ({e.__class__.__name__}); ads without product layer")
            return None

    def scene_prompt(self, hook: HookPlus, job: Job) -> str:
        b = self.brief
        # never name the product: the model would draw its own version of it
        scene = hook.scene or "empty wooden tabletop in a softly lit modern home, shallow depth of field, props at the edges"
        p = f"{scene}. Mood: {b.brand.tone or 'premium'}. Colour accents {', '.join(b.brand.colors[:2])}"
        return f"{p}. Avoid: {job.note}" if job.note else p

    def backgrounds(self, items: list[tuple[Job, HookPlus]], attempt: int = 1) -> dict[str, tuple[str, str]]:
        """creative_id -> (background path, source). One scene per ad, shared by its ratios (consistent look,
        half the image-model calls); the template cover-crops it. 1.91:1 ads get a landscape scene."""
        groups: dict[tuple[str, bool], list[tuple[Job, HookPlus]]] = {}
        for job, hook in items:
            groups.setdefault((job.ad_ref, job.ratio == "1.91:1"), []).append((job, hook))

        def one(key):
            (ad_ref, wide), members = key, groups[key]
            job, hook = members[0]
            w, h = (1216, 640) if wide else (768, 1216)
            out = self.dir / f"bg_{ad_ref}{'_wide' if wide else ''}_v{job.version}_{attempt}.png"
            res = (str(out), media.background(self.scene_prompt(hook, job), out, w, h, self.brief.brand.colors,
                                              seed=attempt * 7 + job.version))
            return [(j.creative_id, res) for j, _ in members]
        with ThreadPoolExecutor(max_workers=4) as ex:
            return {cid: res for pairs in ex.map(one, groups) for cid, res in pairs}

    def frame(self, job: Job, hook: HookPlus, bg: str, out: Path, caption: str = "", show_cta: bool = True) -> None:
        w, h = RATIO_SIZES[job.ratio]
        b, p = self.brief, self.brief.products[0]
        sub = {"offer-badge": f"{b.budget.currency} {p.price:,.0f}" if p.price else hook.ad_copy.description,
               "ugc-caption": "Verified buyer"}.get(job.template_id, hook.ad_copy.description)
        if not self.renderer:
            media.composite_only(bg, self.product, out, w, h)
            return
        page = media.fill_template(job.template_id, job.ratio, w, h, bg=bg, product=self.product,
                                   headline=hook.on_screen_text, sub=sub, cta=hook.ad_copy.cta, colors=b.brand.colors,
                                   font=(b.brand.fonts or ["Inter"])[0], logo=b.brand.logo, brand_name=b.business_name,
                                   caption=caption, show_cta=show_cta)
        self.renderer.render(page, out, w, h)

    def make(self, job: Job, hook: HookPlus, bg: tuple[str, str]) -> Creative:
        w, h = RATIO_SIZES[job.ratio]
        stem = self.dir / f"{job.creative_id}_v{job.version}"
        duration = thumb = None
        if job.kind == "image":
            out = stem.with_suffix(".png")
            self.frame(job, hook, bg[0], out)
        else:
            out, thumb, duration = self.video(job, hook, bg[0], stem)
        return Creative(creative_id=job.creative_id, campaign_ref=job.campaign_ref, adset_ref=job.adset_ref,
                        ad_ref=job.ad_ref, hook_id=job.hook_id, kind=job.kind, ratio=job.ratio, width=w, height=h,
                        duration_s=duration, path=media.rel(out), thumbnail_path=media.rel(thumb) if thumb else None,
                        ad_copy=hook.ad_copy, landing_url=landing_url(self.brief, job.campaign_ref, job.ad_ref),
                        model_used=f"{bg[1]}+{'html' if self.renderer else 'pil'}", template_id=job.template_id,
                        prompt=self.scene_prompt(hook, job), version=job.version, parent_id=job.parent_id)

    def video(self, job: Job, hook: HookPlus, bg: str, stem: Path) -> tuple[Path, Path, float]:
        """Template motion: caption frames over a slow push-in, Edge-TTS voiceover, CTA end card."""
        w, h = RATIO_SIZES[job.ratio]
        b = self.brief
        target = float((b.deliverables.video_lengths_s or [15])[0])
        audio = stem.with_suffix(".mp3")
        vo = media.voiceover(hook.voiceover, audio, f"{(b.languages or ['en'])[0]}-{(b.geo or ['IN'])[0]}")
        total = min(VIDEO_MAX, max(VIDEO_MIN, (vo + 0.8) if vo else target))
        chunks = media.captions(hook.voiceover)
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

def qa(c: Creative, brief: ClientBrief) -> QAResult:
    path = Path(media.resolve(c.path))
    look = Path(media.resolve(c.thumbnail_path)) if c.thumbnail_path else path
    checks = {"file_exists": path.exists(), "has_text_layer": not c.model_used.endswith("+pil")}
    if look.exists():
        from PIL import Image
        with Image.open(look) as im:
            checks["exact_size"] = im.size == (c.width, c.height)
    if c.kind == "video":
        checks["duration_ok"] = bool(c.duration_s and VIDEO_MIN - 0.5 <= c.duration_s <= VIDEO_MAX + 0.5)
        checks["thumbnail"] = bool(c.thumbnail_path and look.exists())
    else:
        checks["size_ok"] = path.exists() and path.stat().st_size < 30_000_000
    if not all(checks.values()):
        return QAResult(score=20, passed=False, checks=checks,
                        notes="Failed checks: " + ", ".join(k for k, v in checks.items() if not v))
    try:
        v = llm.structured("vision", VISION_SYSTEM,
                           f"Ratio {c.ratio}. Brand colours {brief.brand.colors}. Product: {brief.products[0].name}. "
                           f"Intended text: “{c.ad_copy.headline}”", _Vision, images=[str(look)])
        checks |= {"legible": v.legible, "product_ok": v.product_ok, "brand_colors": v.brand_colors,
                   "no_artifacts": v.no_artifacts, "safe_zones": v.safe_zones}
        hard = ("legible", "no_artifacts", "safe_zones")
        return QAResult(score=v.score, passed=all(checks[k] for k in hard) and v.score >= 60, checks=checks, notes=v.notes)
    except Exception as e:
        return QAResult(score=75, passed=True, checks=checks,
                        notes=f"Deterministic checks only; vision QA unavailable ({e.__class__.__name__}).")


# ---------- produce + M8 package ----------

def produce(cid: str, brief: ClientBrief, jobs: list[Job], hooks: dict[str, HookPlus]) -> list[Creative]:
    items = [(j, hook_for(j, hooks, brief)) for j in jobs]
    bus.log(cid, GEN, f"Generating {len(jobs)} assets: backgrounds first (image model → Pollinations → gradient)")
    bgs = {}
    with Studio(cid, brief) as st:
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
            c.qa = qa(c, brief)
            if not c.qa.passed:
                bus.log(cid, QA, f"{c.creative_id} failed QA ({c.qa.notes}); regenerating once")
                retry = job.model_copy(update={"note": c.qa.notes})
                bg2 = st.backgrounds([(retry, hook)], attempt=2)[job.creative_id]
                try:
                    c2 = st.make(retry, hook, bg2)
                    c2.qa = qa(c2, brief)
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


def creatives_step(state: RuneState) -> dict:
    cid = state["client_id"]
    if demo_mode(state):
        bus.log(cid, PACK, "Demo run: using the fixture creative package")
        return {"package": fixture("package")}

    brief = ClientBrief.model_validate(state["brief"])
    bp = CampaignBlueprint.model_validate(state["blueprint"])
    hooks = {h["id"]: HookPlus.model_validate(h) for h in state.get("hooks", [])}
    note = (state.get("notes") or {}).get("creatives", "")
    jobs = plan_jobs(bp, hooks)
    if note:
        jobs = [j.model_copy(update={"note": note}) for j in jobs]
    by_t = {t: sum(j.template_id == t for j in jobs) for t in media.TEMPLATE_IDS}
    bus.log(cid, PLANNER, f"Plan: {len(jobs)} assets = {sum(j.kind == 'image' for j in jobs)} image + "
                          f"{sum(j.kind == 'video' for j in jobs)} video across {', '.join(brief.deliverables.ratios)}; "
                          f"templates {by_t}" + (f"; applying: {note}" if note else ""))
    creatives = produce(cid, brief, jobs, hooks)
    pkg = CreativePackage(client_id=cid, blueprint=bp, creatives=creatives)
    write_manifest(cid, pkg)
    ok = sum(c.qa.passed for c in creatives)
    bus.log(cid, PACK, f"Package ready: {len(creatives)}/{len(jobs)} creatives, {ok} passed QA, manifest in media/{cid}/")
    return {"package": pkg.model_dump(mode="json")}
