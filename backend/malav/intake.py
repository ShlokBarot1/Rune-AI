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
from datetime import date, timedelta
from typing import Optional, TypedDict
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from core import bus, llm, skills
from core.approvals import ask_human
from core.schemas import (BrandKit, BudgetInput, ClientBrief, Deliverables, Goal, KPITargets, Product,
                          Ratio, SpecialAdCategory)
from malav import web
from malav.common import demo_mode, fixture as fixture_json

AGENT = "malav.intake"
CONFIDENT = 0.8
SLOTS = ["goal", "budget", "dates", "geo", "deliverables", "kpis", "special_ad_category"]
GOALS = ["sales", "leads", "traffic", "awareness", "engagement", "app_installs"]
SPECIAL = ["none", "credit", "employment", "housing", "social_issues"]
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
budgets or dates. Prices are numbers in major units. Product image and page URLs must be absolute.
geo = ISO country codes. brand.colors = hex codes that look like brand colours (use the CSS candidates).
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
    }
    return [s for s in SLOTS if not known[s] or d.confidence.get(s, 0) < CONFIDENT]


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
    if slot == "kpis":
        return ("Do you have a target cost per purchase/lead (CPA) or a target ROAS?",
                ["No target yet", "Target CPA ...", "Target ROAS ..."], {})
    first = d.special_ad_category or "none"
    return ("Is this ad about credit, employment, housing or social/political issues? "
            "Meta restricts targeting for these categories.",
            [first, *[c for c in SPECIAL if c != first]], {"special_ad_category": first})


def parse_answer(cid: str, slot: str, q: str, answer: str, options: list[str], default: dict) -> dict:
    a = answer.strip()
    if not a or a == options[0]:
        return default
    if slot in ("goal", "special_ad_category") and a.lower() in options:
        return {slot: a.lower()}
    try:
        patch = llm.structured("fast", PARSE_SYSTEM,
                               f"Today: {date.today().isoformat()}\nQuestion: {q}\nAnswer: {a}", SlotPatch)
        return patch.model_dump(exclude_none=True)
    except Exception as e:
        bus.log(cid, AGENT, f"Couldn't parse '{a}' for {slot} ({e.__class__.__name__}); using {options[0]}")
        return default


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
    brief = fixture_json("brief")
    bus.log(s["client_id"], AGENT, "No website or notes given (or RUNE_OFFLINE=1): using the demo brief")
    b = ClientBrief.model_validate(brief)
    return {"brief": brief, "context_doc": _render_context(b, ContextInsights(), [], 1, "Demo brief (offline).")}


def prefill(s: IntakeState) -> dict:
    cid, url = s["client_id"], _website(s)
    notes = (s.get("intake") or {}).get("notes", "")
    source, colors = "", []
    if url:
        bus.log(cid, AGENT, f"Reading {url} with Jina Reader")
        try:
            source = web.read_page(url)
            bus.log(cid, AGENT, f"Read {len(source):,} characters from the website")
        except Exception as e:
            bus.log(cid, AGENT, f"Couldn't read the website ({e.__class__.__name__}); I'll ask instead")
        try:
            colors = web.page_colors(url)
            bus.log(cid, AGENT, f"CSS colour candidates: {', '.join(colors) or 'none'}")
        except Exception:
            pass

    draft = Draft()
    try:
        user = (f"Website: {url or 'none'}\nCSS colour candidates: {colors}\nClient notes: {notes or 'none'}\n\n"
                f"Website text:\n{source or '(not available)'}")
        draft = llm.structured("fast", EXTRACT_SYSTEM, user, Draft)
        bus.log(cid, AGENT, f"Draft brief: {draft.business_name or '?'}, {len(draft.products)} product(s), "
                            f"colours {draft.brand.colors}, tone '{draft.brand.tone}'",
                draft=draft.model_dump(mode="json", exclude={"confidence"}))
    except Exception as e:
        bus.log(cid, AGENT, f"Draft extraction failed ({e.__class__.__name__}); I'll ask for everything")
    if not draft.brand.colors and colors:
        draft.brand.colors = colors[:3]

    pending = missing(draft)
    bus.log(cid, AGENT, f"Need to ask about: {', '.join(pending)}" if pending else "Every slot prefilled with confidence")
    return {"draft": draft.model_dump(mode="json"), "pending": pending, "source": source, "answers": []}


def ask(s: IntakeState) -> dict:
    """One question per execution. ask_human comes first; only cheap string building precedes it."""
    cid, slot = s["client_id"], s["pending"][0]
    d = Draft.model_validate(s["draft"])
    q, options, default = question(slot, d)
    answer = ask_human(cid, AGENT, q, options)
    patch = parse_answer(cid, slot, q, answer, options, default)
    if slot == "budget" and answer:
        patch["budget_notes"] = answer
    d = Draft.model_validate({**d.model_dump(), **patch, "confidence": {**d.confidence, slot: 1.0}})
    bus.log(cid, AGENT, f"{slot}: {answer or options[0]}", patch=json.loads(json.dumps(patch, default=str)))
    return {"draft": d.model_dump(mode="json"), "pending": s["pending"][1:],
            "answers": [*s.get("answers", []), [q, answer or options[0]]]}


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
    )
    bus.log(cid, AGENT, f"Brief ready: {brief.business_name}, {brief.goal}, {brief.budget.total:,.0f} "
                        f"{brief.budget.currency} {brief.budget.start}→{brief.budget.end}, geo {brief.geo}")
    _sanity_check(cid, brief)
    doc = context_doc(cid, brief, s.get("source", ""), s.get("answers"))
    return {"brief": brief.model_dump(mode="json"), "context_doc": doc}


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
        brief = new.model_copy(update={"client_id": cid})
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
