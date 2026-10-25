"""M1 Intake Agent: website prefill -> one question per missing slot -> ClientBrief + context doc.

A small LangGraph subgraph used directly as the "intake" Step's node:

  START -> fixture                      (RUNE_OFFLINE=1 or no website/notes given: demo brief)
        -> revise -> END                (rerun after a gate with state["notes"]["intake"])
        -> prefill -> ask* -> finalize  (normal run; `ask` loops, one question per execution)

Each question is its own node execution, so resuming after a human answer never repeats the
website scrape or earlier questions. Only `brief` and `context_doc` leave the subgraph.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from typing import Literal, Optional, TypedDict
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from core import bus, llm, skills
from core.approvals import ask_human
from core import compliance
from core.schemas import (BrandKit, BudgetInput, ClientBrief, Deliverables, Goal, KPITargets, Location, Offer,
                          Product, Ratio, SpecialAdCategory)
from malav import lineup, media, own, web
from malav.common import demo_mode, fixture as fixture_json

AGENT = "malav.intake"
CONFIDENT = 0.8
SLOTS = ["goal", "budget", "dates", "geo", "deliverables", "kpis", "special_ad_category", "visual"]
GOALS = ["sales", "leads", "traffic", "awareness", "engagement", "app_installs"]
SPECIAL = ["none", "credit", "employment", "housing", "social_issues"]
# the "visual" slot: what the ads show (ClientBrief.creative_focus)
BRAND_OPT = "General brand or category scenes (AI-made)"
PHOTOS_OPT = "Real photos of our place, team, work or app"
PRODUCT_OPT = "Our products (I'll upload photos)"
RANGE_OPT = "Our range: several products per ad, plus why to buy"
NO_PHOTO = BRAND_OPT  # creatives.ensure_visual offers the same way out
# a free-text answer that isn't an image means "no single product" (e.g. "make it general", "skip")
IMAGE_REF = re.compile(r"^(https?://\S+|media/\S+|\S+\.(png|jpe?g|webp))$", re.I)
SKILLS = ["product-marketing", "customer-research", "offers"]  # PLAN.md §3.1, M1


# ---------- models the LLM fills ----------

class Draft(BaseModel):
    """Everything intake knows so far. Slot fields stay None until known."""
    business_name: str = ""
    industry: str = ""
    products: list[Product] = []
    brand: BrandKit = BrandKit()
    audience_notes: str = ""
    languages: list[str] = []
    goal: Optional[Goal] = None
    currency: Optional[str] = None
    budget_total: Optional[float] = None
    budget_notes: str = ""              # client's own budget wording (Shlok checks it for GST/fees)
    start: Optional[date] = None
    end: Optional[date] = None
    geo: list[str] = []
    n_images: Optional[int] = None
    n_videos: Optional[int] = None
    ratios: list[Ratio] = []
    target_cpa: Optional[float] = None
    target_roas: Optional[float] = None
    special_ad_category: Optional[SpecialAdCategory] = None
    creative_focus: Optional[Literal["product", "photos", "brand"]] = None
    photos: list[str] = []
    locations: list[Location] = []
    offer: Optional[Offer] = None
    confidence: dict[str, float] = {}   # slot name -> 0..1


class SlotPatch(BaseModel):
    """The fields one human answer can set. Unmentioned fields stay null."""
    goal: Optional[Goal] = None
    currency: Optional[str] = None
    budget_total: Optional[float] = None
    start: Optional[date] = None
    end: Optional[date] = None
    geo: Optional[list[str]] = None
    n_images: Optional[int] = None
    n_videos: Optional[int] = None
    ratios: Optional[list[Ratio]] = None
    target_cpa: Optional[float] = None
    target_roas: Optional[float] = None
    special_ad_category: Optional[SpecialAdCategory] = None


class ContextInsights(BaseModel):
    one_liner: str = ""
    what_it_does: str = ""
    category: str = ""
    product_type: str = ""
    primary_use_case: str = ""
    jobs_to_be_done: list[str] = []
    core_problem: str = ""
    emotional_tension: str = ""
    differentiators: list[str] = []
    objections: list[str] = []
    customer_phrases: list[str] = []
    voice_attributes: list[str] = []
    proof_points: list[str] = []
    offer: str = ""


EXTRACT_SYSTEM = """You are the intake agent of an ad agency. From the client's website text and notes,
extract a draft brief. Use only facts in the input; leave unknown fields null or empty, never guess
budgets or dates. products: the brand's core products first (what it is known for and sells most),
not merchandise or accessories featured on the page. Prices are numbers in major units. Product image and page URLs must be absolute.
geo = ISO country codes. brand.colors = hex codes that look like brand colours (use the CSS candidates).
creative_focus (what the ads should show): "product" for a business selling physical products when the notes
don't say otherwise; "range" when the notes ask to showcase a range, line, collection or a whole category the
brand sells (e.g. "a general campaign for our new skincare line"); "brand" for pure brand awareness or a cause with
no products to sell; null when unsure (services, local businesses, apps, events: the client
is asked whether to use their own photos). Give its confidence under "visual".
locations: the physical places customers visit (stores, branches, clinics, studios, restaurants) with full
addresses as written on the site or notes; leave empty for online-only businesses. radius_km: 3 in a dense city,
8 in a town, 15 for a destination business, unless the notes say otherwise.
offer: the promotion currently on the site or in the notes (headline: just the deal, like "30% off", without the code; kind, coupon code, was/now
price, last day as a date); null when there is none.
languages: ISO 639-1 codes of the languages the ads should be in, from the notes and the site (e.g. ["hi", "en"]);
["en"] when nothing says otherwise.
confidence: for each of goal, budget, dates, geo, deliverables, kpis, special_ad_category give 0..1 for
how sure the input makes you (0 when not mentioned)."""

PARSE_SYSTEM = """Turn the client's answer to one intake question into JSON. Set only the fields the
answer gives; leave everything else null. Money is in major units (1.5 lakh = 150000, 50k = 50000).
Dates are ISO (YYYY-MM-DD); resolve relative dates against today. Countries are ISO-2 codes."""

CONTEXT_SYSTEM = """You write the client's product-marketing context (the foundation every other
marketing skill reads). Fill each field from the brief and website text only; short, specific,
verbatim customer language where available. Leave a field empty rather than invent facts."""

REVISE_SYSTEM = """You update a client brief for an ad agency. Apply the human's instruction exactly
and change nothing else. Keep client_id unchanged."""


# ---------- state ----------

class IntakeIn(TypedDict, total=False):
    client_id: str
    intake: dict
    notes: dict
    brief: dict
    context_doc: str


class IntakeOut(TypedDict, total=False):
    brief: dict
    context_doc: str


class IntakeState(IntakeIn, total=False):
    draft: dict
    pending: list
    source: str          # website text excerpt, reused for the context doc
    answers: list        # [question, answer] pairs, shown in the context doc
    ranged: bool         # the site was searched for the range already (lineup.find_range)


# ---------- helpers ----------

def _website(s: IntakeState) -> str:
    i = s.get("intake") or {}
    url = (i.get("website") or i.get("url") or "").strip()
    return url if not url or url.startswith("http") else f"https://{url}"


def _flight(today: Optional[date] = None) -> tuple[date, date]:
    start = (today or date.today()) + timedelta(days=7)
    return start, start + timedelta(days=29)


def missing(d: Draft) -> list[str]:
    known = {
        "goal": d.goal is not None,
        "budget": d.budget_total is not None,
        "dates": d.start is not None and d.end is not None,
        "geo": bool(d.geo),
        "deliverables": d.n_images is not None and d.n_videos is not None and bool(d.ratios),
        "kpis": d.target_cpa is not None or d.target_roas is not None,
        "special_ad_category": d.special_ad_category is not None,
        "visual": visual_ready(d),
    }
    # visual: asked only when its assets are missing; a product site with real photos needs no question
    return [s for s in SLOTS if not known[s] or (s != "visual" and d.confidence.get(s, 0) < CONFIDENT)]


def question(slot: str, d: Draft) -> tuple[str, list[str], dict]:
    """(question, options, patch for options[0]). options[0] is the suggested answer (auto mode picks it)."""
    cur = d.currency or "INR"
    if slot == "goal":
        first = d.goal or "sales"
        return ("What is the main goal of these ads?", [first, *[g for g in GOALS if g != first]], {"goal": first})
    if slot == "budget":
        total = d.budget_total or 30000
        return (f"What is the total ad budget for this campaign, and in which currency? (e.g. {total:,.0f} {cur})",
                [f"{total:,.0f} {cur}"], {"budget_total": float(total), "currency": cur})
    if slot == "dates":
        start, end = d.start or _flight()[0], d.end or _flight()[1]
        return ("When should the campaign start and end?", [f"{start.isoformat()} to {end.isoformat()}"],
                {"start": start, "end": end})
    if slot == "geo":
        geo = d.geo or ["IN"]
        return ("Which countries should the ads run in?", [", ".join(geo)], {"geo": geo})
    if slot == "deliverables":
        ni = 2 if d.n_images is None else d.n_images
        nv = 2 if d.n_videos is None else d.n_videos
        ratios = d.ratios or ["4:5", "9:16"]
        return ("How many image ads and video ads do you want, and in which ratios (1:1, 4:5, 9:16, 1.91:1)?",
                [f"{ni} images, {nv} videos, {' + '.join(ratios)}"], {"n_images": ni, "n_videos": nv, "ratios": ratios})
    if slot == "visual":
        q, options, _ = visual_question(d)
        return q, options, {"creative_focus": "brand"}
    if slot == "kpis":
        return ("Do you have a target cost per purchase/lead (CPA) or a target ROAS?",
                ["No target yet", "Target CPA ...", "Target ROAS ..."], {})
    first = d.special_ad_category or "none"
    return ("Is this ad about credit, employment, housing or social/political issues? "
            "Meta restricts targeting for these categories.",
            [first, *[c for c in SPECIAL if c != first]], {"special_ad_category": first})


CURRENCIES = [("USD", r"\$|\busd\b|dollar"), ("INR", r"₹|\binr\b|\brs\b|rupee"), ("EUR", r"€|\beur\b|euro"),
              ("GBP", r"£|\bgbp\b|pound"), ("AED", r"\baed\b|dirham")]
SCALE = {"k": 1e3, "thousand": 1e3, "l": 1e5, "lac": 1e5, "lakh": 1e5, "lakhs": 1e5, "cr": 1e7, "crore": 1e7,
         "m": 1e6, "mn": 1e6, "million": 1e6}


def quick_parse(slot: str, a: str, default: dict) -> dict:
    """Plain answers ("10000 dollars", "1 image 1 video", "CPA 500") without a model call; {} when unsure."""
    low = a.lower().replace(",", "")
    if slot == "budget":
        m = re.search(r"(\d+(?:\.\d+)?)\s*(k|thousand|lakhs?|lac|l|crore|cr|million|mn|m)?\b", low)
        if not m:
            return {}
        cur = next((c for c, pat in CURRENCIES if re.search(pat, low)), default["currency"])
        return {"budget_total": float(m.group(1)) * SCALE.get(m.group(2) or "", 1), "currency": cur}
    if slot == "dates":
        d = re.findall(r"\d{4}-\d{2}-\d{2}", low)
        return {"start": date.fromisoformat(d[0]), "end": date.fromisoformat(d[1])} if len(d) == 2 else {}
    if slot == "deliverables":
        ni = re.search(r"(\d+)\s*(?:image|static|photo|banner)", low)
        nv = re.search(r"(\d+)\s*(?:video|reel|clip)", low)
        ratios = [r for r in ("1:1", "4:5", "9:16", "1.91:1") if r in low]
        if not (ni or nv or ratios):
            return {}
        return {"n_images": int(ni.group(1)) if ni else (0 if nv else default["n_images"]),
                "n_videos": int(nv.group(1)) if nv else (0 if ni else default["n_videos"]),
                "ratios": ratios or default["ratios"]}
    if slot == "kpis":
        cpa = re.search(r"cpa\D{0,15}(\d+(?:\.\d+)?)", low)
        roas = re.search(r"roas\D{0,15}(\d+(?:\.\d+)?)", low)
        return {k: float(m.group(1)) for k, m in (("target_cpa", cpa), ("target_roas", roas)) if m}
    return {}


def parse_answer(cid: str, slot: str, q: str, answer: str, options: list[str], default: dict) -> Optional[dict]:
    """The slot patch for an answer; None when it can't be understood (the caller asks again)."""
    a = answer.strip()
    if not a or a == options[0]:
        return default
    if slot in ("goal", "special_ad_category") and a.lower() in options:
        return {slot: a.lower()}
    if quick := quick_parse(slot, a, default):
        return quick
    try:
        patch = llm.structured("fast", PARSE_SYSTEM,
                               f"Today: {date.today().isoformat()}\nQuestion: {q}\nAnswer: {a}", SlotPatch)
        return patch.model_dump(exclude_none=True)
    except Exception:
        return None


# ---------- what the ads show (shared with creatives.ensure_visual; works on a Draft or a ClientBrief) ----------

def visual_ready(x, verify: bool = False) -> bool:
    """Has everything the chosen focus needs: product -> a real photo, photos -> at least one photo, brand -> nothing.
    verify: re-check product photos (a brief edited by hand may hold a logo again)."""
    if x.creative_focus == "brand":
        return True
    if x.creative_focus == "photos":
        return bool(x.photos)
    if x.creative_focus in ("product", "range"):
        first = [p.images[0] for p in x.products if p.images]
        enough = len(first) >= (2 if x.creative_focus == "range" else 1)  # a range of one is a product ad
        return enough and (not verify or sum(media.product_photo_ok(i) for i in first) >= (2 if x.creative_focus == "range" else 1))
    return False


def visual_question(x) -> tuple[str, list[str], str]:
    """(question, options, upload button label or "" for none). Options[0] never needs a file, so an auto-approved
    run can't loop on it."""
    if x.creative_focus == "range":
        need = [p.name for p in x.products if not p.images][:8]
        have = [p.name for p in x.products if p.images]
        return ("To show the range I need real photos of at least 2 products"
                + (f" (have: {', '.join(have[:6])})" if have else "")
                + (f". Upload one photo each for: {', '.join(need)}, in that order" if need else ". Upload product photos")
                + ". Extra photos become new products, named after their files.", [BRAND_OPT, PRODUCT_OPT],
                "Upload product photos")
    if x.creative_focus == "product":
        names = ", ".join(p.name for p in x.products[:4]) or "your product"
        return (f"The ads should show {names}, but I couldn't find a real photo (only logos or icons). Upload one "
                "photo per product, in that order, or paste image URLs (one per line).",
                [BRAND_OPT, PHOTOS_OPT], "Upload product photos")
    if x.creative_focus == "photos":
        return ("Upload real photos the ads can use as their main image: your place, team, work, food, event or app "
                "screens. Bright, uncluttered photos work best.", [BRAND_OPT], "Upload photos")
    return ("What should the ads show? A range sells a category (several products per ad, carousels); real photos "
            "make services, local businesses and apps trustworthy; one product needs its photo; general scenes suit "
            "pure brand awareness.", [BRAND_OPT, RANGE_OPT, PHOTOS_OPT, PRODUCT_OPT], "")


def visual_patch(x, answer: str) -> Optional[dict]:
    """An answer to visual_question -> fields to update on x; None when it can't be used (ask again).
    Images go to the products still without a photo (in order; extra ones become new products named after their
    files) or to photos, per the current focus."""
    lines = [line.strip() for line in answer.strip().splitlines() if line.strip()]
    images = [line for line in lines if IMAGE_REF.match(line)]
    if images:
        good = [i for i in images if media.product_photo_ok(i)]
        if not good:
            return None
        if x.creative_focus == "photos":
            return {"photos": [*good, *x.photos]}
        products = [p.model_copy() for p in x.products] or [Product(name=getattr(x, "business_name", "") or "Product")]
        slots = [i for i, p in enumerate(products) if not p.images] + [i for i, p in enumerate(products) if p.images]
        for img in good:
            if slots:
                i = slots.pop(0)
                products[i] = products[i].model_copy(update={"images": [img, *products[i].images]})
            elif x.creative_focus == "range":
                stem = re.split(r"[\\/]", img.split("?")[0])[-1].rsplit(".", 1)[0]
                products.append(Product(name=re.sub(r"[_-]+", " ", stem).strip().title() or "Product", images=[img]))
        focus = "range" if x.creative_focus == "range" else "product"
        return {"products": [p.model_dump(mode="json") for p in products], "creative_focus": focus}
    low = answer.strip().lower()
    if low == RANGE_OPT.lower() or re.search(r"\brange\b|line-?up|collection|variety|catalog|all (of )?(our|the) products|"
                                              r"many products|several products", low):
        return {"creative_focus": "range"}
    if low == PRODUCT_OPT.lower() or re.fullmatch(r"(our |the )?products?( ads?)?", low):
        return {"creative_focus": "product"}
    if low == PHOTOS_OPT.lower() or re.search(r"\b(our|own|real) (photos?|pictures?|images?)|\bphotos? of\b", low):
        return {"creative_focus": "photos"}
    if low == BRAND_OPT.lower() or re.search(r"general|brand|category|scenes?|awareness|skip|no (product|photo)|without", low):
        return {"creative_focus": "brand"}
    return None


def apply_visual(x, patch: dict):
    """x (Draft or ClientBrief) with a visual_patch applied."""
    return type(x).model_validate({**x.model_dump(mode="json"), **patch})


def _render_context(b: ClientBrief, ins: ContextInsights, answers: list, version: int, change: str) -> str:
    today = date.today().isoformat()
    bullets = lambda xs: "\n".join(f"- {x}" for x in xs) if xs else "- _to be filled by research (M2)_"
    quotes = lambda xs: "\n".join(f'- "{x}"' for x in xs) if xs else "- _to be filled by research (M2)_"
    p0 = b.products[0]
    products = "\n".join(
        f"- **{p.name}**" + (f" — {b.budget.currency} {p.price:,.0f}" if p.price else "")
        + (f": {'; '.join(p.usp)}" if p.usp else "") for p in b.products)
    kpi = ", ".join(x for x in [
        f"target CPA {b.kpi_targets.target_cpa:,.0f} {b.budget.currency}" if b.kpi_targets.target_cpa else "",
        f"target ROAS {b.kpi_targets.target_roas}" if b.kpi_targets.target_roas else "",
    ] if x) or "not set"
    qa = "\n".join(f"- {q} → {a}" for q, a in answers) or "- (none, all prefilled)"
    return f"""# Product Marketing Context

**Document version:** v{version}
**Last updated:** {today}

## Product Overview
**One-liner:** {ins.one_liner or f"{b.business_name}: {p0.name}"}
**What it does:** {ins.what_it_does or (", ".join(p0.usp) or "_TBD_")}
**Product category:** {ins.category or b.industry}
**Product type:** {ins.product_type or b.industry}
**Business model:** {ins.offer or "_TBD_"}
**Products:**
{products}

## Target Audience
**Who:** {b.audience_notes or "_to be filled by research (M2)_"}
**Primary use case:** {ins.primary_use_case or "_TBD_"}
**Jobs to be done:**
{bullets(ins.jobs_to_be_done)}

## Personas
_Built by research (M2)._

## Problems & Pain Points
**Core problem:** {ins.core_problem or "_TBD_"}
**Emotional tension:** {ins.emotional_tension or "_TBD_"}

## Competitive Landscape
_Built by the competitor analyst (M2)._

## Differentiation
**Key differentiators:**
{bullets(ins.differentiators or [u for p in b.products for u in p.usp])}

## Objections
{bullets(ins.objections)}

## Customer Language
**How they describe it:**
{quotes(ins.customer_phrases)}
**Words to use:** {", ".join(b.brand.do_words) or "_TBD_"}
**Words to avoid:** {", ".join(b.brand.dont_words) or "_none given_"}

## Brand Voice
**Tone:** {b.brand.tone or "_TBD_"}
**Attributes:** {", ".join(ins.voice_attributes) or "_TBD_"}
**Colors:** {", ".join(b.brand.colors) or "_TBD_"}  **Fonts:** {", ".join(b.brand.fonts) or "_TBD_"}

## Proof Points
{bullets(ins.proof_points)}

## Goals
**Business goal:** {b.goal}
**Conversion action:** {b.goal} on {", ".join(b.landing_pages) or b.website or "_TBD_"}

## Paid Ads
**Target CPA / ROAS:** {kpi}
**Budget and pacing:** {b.budget.total:,.0f} {b.budget.currency} from {b.budget.start} to {b.budget.end}
**Geo / languages:** {", ".join(b.geo)} / {", ".join(b.languages)}
**Deliverables:** {b.deliverables.n_images} image ads, {b.deliverables.n_videos} video ads; ratios {", ".join(b.deliverables.ratios)}
**Special ad category:** {b.special_ad_category}
**Constraints:** {"; ".join(b.constraints) or "none"}
**Agent may:** read, compare, flag, draft, recommend
**Agent may not (without approval):** change budgets or bids, pause or launch, edit tracking, publish ads

## Intake answers
{qa}

## Learnings
| Date | Learning | Evidence | Applies to | Status |
|------|----------|----------|------------|--------|

## Changelog
- v{version} ({today}) — {change}
"""


def context_doc(cid: str, b: ClientBrief, source: str = "", answers: Optional[list] = None,
                version: int = 1, change: str = "Initial context from intake.") -> str:
    try:
        bus.log(cid, AGENT, f"Writing product-marketing context (skills: {', '.join(SKILLS)})")
        system = skills.with_skills(CONTEXT_SYSTEM, SKILLS)
        user = f"Brief:\n{b.model_dump_json()}\n\nWebsite text:\n{source[:6000]}"
        ins = llm.structured("fast", system, user, ContextInsights)
    except Exception as e:
        bus.log(cid, AGENT, f"Context insights unavailable ({e.__class__.__name__}); writing doc from the brief only")
        ins = ContextInsights()
    return _render_context(b, ins, answers or [], version, change)


# ---------- nodes ----------

def fixture(s: IntakeState) -> dict:
    brief = fixture_json("brief", s["client_id"])
    bus.log(s["client_id"], AGENT, "No website or notes given (or RUNE_OFFLINE=1): using the demo brief")
    b = ClientBrief.model_validate(brief)
    return {"brief": brief, "context_doc": _render_context(b, ContextInsights(), [], 1, "Demo brief (offline).")}


def prefill(s: IntakeState) -> dict:
    cid, url = s["client_id"], _website(s)
    notes = (s.get("intake") or {}).get("notes", "")
    source, colors = "", []
    if url:
        bus.log(cid, AGENT, f"Reading {url}")
        try:
            source = web.read_page(url)
            bus.log(cid, AGENT, "Read the website")
        except Exception as e:
            bus.log(cid, AGENT, f"Couldn't read the website ({e.__class__.__name__}); I'll ask instead")
        try:
            colors = web.page_colors(url)
            bus.log(cid, AGENT, f"Colours used on the site: {', '.join(colors) or 'none found'}")
        except Exception:
            pass

    draft = Draft()
    try:
        user = (f"Website: {url or 'none'}\nCSS colour candidates: {colors}\nClient notes: {notes or 'none'}\n\n"
                f"Website text:\n{source or '(not available)'}")
        draft = llm.structured("fast", EXTRACT_SYSTEM, user, Draft)
        bus.log(cid, AGENT, f"First draft of the brief: {draft.business_name or 'name unknown'}, "
                            f"{len(draft.products)} product(s), brand colours {', '.join(draft.brand.colors) or 'none'}"
                            + (f", tone {draft.brand.tone}" if draft.brand.tone else ""),
                draft=draft.model_dump(mode="json", exclude={"confidence"}))
    except Exception as e:
        bus.log(cid, AGENT, f"Draft extraction failed ({e.__class__.__name__}); I'll ask for everything")
    if not draft.brand.colors and colors:
        draft.brand.colors = colors[:3]
    for p in draft.products:  # logos and app icons are not product photos (see media.product_photo_ok)
        p.images = [i for i in p.images[:3] if media.product_photo_ok(i)]
    for p in draft.products[:3]:  # the creatives composite the REAL product photo, so make sure we have one
        if not p.images and p.url and p.url.rstrip("/") != url.rstrip("/"):  # a homepage og:image is usually the logo
            try:
                img = web.og_image(p.url)
                if img and media.product_photo_ok(img):
                    p.images = [img]
                    bus.log(cid, AGENT, f"Product photo for {p.name}: {img[:90]}")
            except Exception:
                pass

    ranged = draft.creative_focus == "range"
    if ranged:  # the notes asked for a range: read it off the site before asking for photos
        draft.products = lineup.find_range(cid, url, notes or f"{draft.business_name} {draft.industry}", draft.products)
    if items := own.uploads(s):
        d = own.deliverables(own.probe_all(cid, items))
        draft = draft.model_copy(update={**d, "confidence": {**draft.confidence, "deliverables": 1.0}})
        bus.log(cid, AGENT, f"You supplied your own creatives: {d['n_images']} image(s), {d['n_videos']} video(s) in "
                            f"{', '.join(d['ratios'])}. The ads run on exactly these; nothing is generated")
    pending = missing(draft)
    if own.uploads(s):  # the client's finished ads: nothing about the picture to decide
        pending = [x for x in pending if x != "visual"]
    bus.log(cid, AGENT, f"Need to ask about: {', '.join(pending)}" if pending else "Every slot prefilled with confidence")
    return {"draft": draft.model_dump(mode="json"), "pending": pending, "source": source, "answers": [],
            "ranged": ranged}


def ask(s: IntakeState) -> dict:
    """One question per execution. ask_human comes first; only cheap string building precedes it."""
    cid, slot = s["client_id"], s["pending"][0]
    slot = "visual" if slot == "product_photo" else slot  # runs paused on the older product-photo question
    d = Draft.model_validate(s["draft"])
    q, options, default = question(slot, d)
    upload = visual_question(d)[2] if slot == "visual" else ""
    answer = ask_human(cid, AGENT, q, options, upload=upload)
    patch = visual_patch(d, answer) if slot == "visual" else parse_answer(cid, slot, q, answer, options, default)
    if patch is None:  # never swap in the suggested value silently: ask the same question again
        bus.log(cid, AGENT, f"That image looks like a logo or icon, or is under {media.MIN_PRODUCT_SIDE}px: please send a "
                            "real photo, or pick an option" if slot == "visual" else
                f"I couldn't understand “{answer}”. Please answer like “{options[0]}”, or pick the suggestion")
        return {"pending": s["pending"]}
    if slot == "budget" and answer:
        patch["budget_notes"] = answer
    d = Draft.model_validate({**d.model_dump(), **patch, "confidence": {**d.confidence, slot: 1.0}})
    shown = {"brand": "general brand / category scenes", "photos": "your own photos", "product": "your products",
             "range": "your range (several products, carousels, benefit tags)"}
    said = f"ads show {shown[d.creative_focus]}" if slot == "visual" else (answer or options[0])
    bus.log(cid, AGENT, f"{slot}: {said}", patch=json.loads(json.dumps(patch, default=str)))
    ranged = s.get("ranged", False)
    if slot == "visual" and d.creative_focus == "range" and not ranged:  # just chosen: read the range off the site
        notes = (s.get("intake") or {}).get("notes", "")
        d.products = lineup.find_range(cid, _website(s), notes or f"{d.business_name} {d.industry}", d.products)
        ranged = True
    rest = s["pending"][1:]
    if slot == "visual" and not visual_ready(d):  # e.g. "use our photos": next, the upload itself
        rest = ["visual", *rest]
    return {"draft": d.model_dump(mode="json"), "pending": rest, "ranged": ranged,
            "answers": [*s.get("answers", []), [q, said]]}


def finalize(s: IntakeState) -> dict:
    cid, url = s["client_id"], _website(s)
    d = Draft.model_validate(s["draft"])
    start, end = _flight()
    domain = re.sub(r"^www\.", "", urlparse(url).netloc).split(".")[0].title() if url else ""
    name = d.business_name or (s.get("intake") or {}).get("business_name") or domain or "Client"
    products = d.products or [Product(name=name, url=url or None)]
    landing = [p.url for p in products if p.url] or ([url] if url else [])
    brief = ClientBrief(
        client_id=cid, business_name=name, website=url or None, industry=d.industry or "unknown",
        products=products, goal=d.goal or "sales",
        kpi_targets=KPITargets(target_cpa=d.target_cpa, target_roas=d.target_roas),
        geo=d.geo or ["IN"], languages=d.languages or ["en"], audience_notes=d.audience_notes, brand=d.brand,
        budget=BudgetInput(total=d.budget_total or 30000, currency=(d.currency or "INR").upper(),
                           start=d.start or start, end=d.end or end, notes=d.budget_notes),
        deliverables=Deliverables(n_images=2 if d.n_images is None else d.n_images,
                                  n_videos=2 if d.n_videos is None else d.n_videos,
                                  ratios=d.ratios or ["4:5", "9:16"]),
        special_ad_category=d.special_ad_category or "none", landing_pages=landing,
        creative_focus=d.creative_focus or ("product" if any(p.images for p in products) else "brand"),
        photos=d.photos, locations=d.locations, offer=d.offer,
    )
    brief = enrich(cid, brief)
    bus.log(cid, AGENT, f"Brief ready: {brief.business_name}, {brief.goal}, {brief.budget.total:,.0f} "
                        f"{brief.budget.currency} {brief.budget.start}→{brief.budget.end}, geo {brief.geo}")
    _sanity_check(cid, brief)
    doc = context_doc(cid, brief, s.get("source", ""), s.get("answers"))
    return {"brief": brief.model_dump(mode="json"), "context_doc": doc}


def enrich(cid: str, brief: ClientBrief) -> ClientBrief:
    """What follows from the brief rather than from the client: map pins for its locations, restricted categories
    (and the Meta special category they force), whether catalog ads apply. Run after intake and after edits."""
    locs = []
    for loc in brief.locations:  # radius targeting needs coordinates; Nominatim asks for <= 1 request/s
        if loc.lat is None and (loc.address or loc.city):
            try:
                hit = web.geocode(", ".join(x for x in (loc.address, loc.city, loc.country or brief.geo[0]) if x))
                if hit:
                    loc = loc.model_copy(update={"lat": hit[0], "lng": hit[1]})
                time.sleep(1)
            except Exception as e:
                bus.log(cid, AGENT, f"Couldn't place {loc.name} on the map ({e.__class__.__name__}); targeting its city")
        locs.append(loc)
    rules = compliance.check(compliance.brief_text(brief))
    special = next((r.special for r in rules if r.special), None)
    from shlok import catalog
    many = len(catalog.eligible(brief)) >= catalog.MIN_PRODUCTS
    out = brief.model_copy(update={
        "locations": locs, "restricted": sorted({r.category for r in rules} | set(brief.restricted)),
        "special_ad_category": brief.special_ad_category if brief.special_ad_category != "none" or not special else special,
        "catalog_ads": brief.catalog_ads or (many and brief.goal == "sales" and brief.creative_focus in ("product", "range")),
    })
    for r in rules:
        bus.log(cid, AGENT, {"blocked": f"Meta doesn't allow ads for {r.category}. {r.copy_rules}",
                             "authorization": f"{r.category}: needs Meta's written authorisation before go-live "
                                              f"(confirm it in the brief). {r.copy_rules}",
                             "restricted": f"{r.category}: restricted category. {r.copy_rules}"}[r.action])
    if out.locations:
        bus.log(cid, AGENT, "Local business: ads reach people around " + ", ".join(
            f"{x.name} ({x.radius_km:g} km)" + ("" if x.lat is not None else " (city level)") for x in out.locations))
    if out.catalog_ads and not brief.catalog_ads:
        bus.log(cid, AGENT, f"{len(catalog.eligible(brief))} products with prices and photos: adding catalog ads, so "
                            "Meta shows each person the products they're likeliest to buy")
    return out


def _sanity_check(cid: str, brief: ClientBrief) -> None:
    """Early budget sanity check with Shlok's tool (PLAN M1): can this budget carry a 2-ad-set test?"""
    try:
        from shlok.budget import budget_feasibility
        r = budget_feasibility(brief, n_adsets=2, asked_by=AGENT)
        bus.log(cid, AGENT, ("Budget check: " if r.feasible else "Heads-up for strategy: ") + r.suggestion)
    except Exception as e:
        bus.log(cid, AGENT, f"Budget sanity check skipped ({e.__class__.__name__})")


def revise(s: IntakeState) -> dict:
    """Gate rerun: apply the human's instruction to the existing brief instead of re-asking everything."""
    cid, note = s["client_id"], s["notes"]["intake"]
    brief = ClientBrief.model_validate(s["brief"])
    bus.log(cid, AGENT, f"Applying your change: {note}")
    try:
        new = llm.structured("fast", REVISE_SYSTEM, f"Brief:\n{brief.model_dump_json()}\n\nInstruction: {note}",
                             ClientBrief)
        brief = enrich(cid, new.model_copy(update={"client_id": cid}))
        bus.log(cid, AGENT, "Brief updated")
    except Exception as e:
        bus.log(cid, AGENT, f"Couldn't apply the change automatically ({e.__class__.__name__}); brief unchanged")
    m = re.search(r"\*\*Document version:\*\* v(\d+)", s.get("context_doc") or "")
    doc = context_doc(cid, brief, version=int(m.group(1)) + 1 if m else 2, change=f"Human change: {note[:120]}")
    return {"brief": brief.model_dump(mode="json"), "context_doc": doc}


def _route_start(s: IntakeState) -> str:
    if (s.get("notes") or {}).get("intake") and s.get("brief"):
        return "revise"
    return "fixture" if demo_mode(s) else "prefill"


def _route_next(s: IntakeState) -> str:
    return "ask" if s.get("pending") else "finalize"


def build():
    g = StateGraph(IntakeState, input_schema=IntakeIn, output_schema=IntakeOut)
    for name, fn in [("fixture", fixture), ("revise", revise), ("prefill", prefill), ("ask", ask), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_conditional_edges(START, _route_start, ["fixture", "revise", "prefill"])
    g.add_conditional_edges("prefill", _route_next, ["ask", "finalize"])
    g.add_conditional_edges("ask", _route_next, ["ask", "finalize"])
    for n in ("fixture", "revise", "finalize"):
        g.add_edge(n, END)
    return g.compile()  # no checkpointer: inherits the parent graph's, so interrupts resume here


graph = build()
