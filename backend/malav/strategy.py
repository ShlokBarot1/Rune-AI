"""M3 Strategy Agent: research -> CampaignBlueprint, negotiated with Shlok's budget agent.

The LLM ranks ad-set ideas (persona x angle) and picks the funnel stage; code builds the tree so
refs, deliverable counts and ratios are always right:
  * objective comes from GOAL_TO_OBJECTIVE
  * budget_feasibility() decides how many ad sets the money supports (visible negotiation on the bus)
  * ads = exactly the client's image/video deliverables spread over the ad sets; ad sets are capped so
    each gets at least MIN_ADS of them where the deliverables allow (never padded with extra ads)
  * every ad gets a hook slot id (h_<adset>_<n>) that the hooks agent fills
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from core import bus, calendar, llm, skills
from core.schemas import (GOAL_TO_OBJECTIVE, AdPlan, AdSetPlan, CampaignBlueprint, CampaignPlan, ClientBrief,
                          FunnelStage, ResearchReport)
from core.state import RuneState
from malav import own
from shlok import audiences
from malav.hooks import _cut
from malav.common import demo_mode, fixture
from shlok.budget import budget_feasibility

AGENT = "malav.strategy"
MAX_ADSETS = 4
MIN_ADS = 2


class AdSetIdea(BaseModel):
    persona_ref: str
    angle: str           # short, e.g. "cafe price vs ₹55/cup"
    product: str = ""    # which brief product this ad set sells (catalogs); "" = brand level
    name: str = ""
    rationale: str = ""  # cite the research evidence


class _Plan(BaseModel):
    funnel_stage: FunnelStage = "TOF"
    ideas: list[AdSetIdea]   # best first
    rationale: str = ""


SYSTEM = """You are the strategy agent of a Meta ads agency. Propose 2-4 ad sets, each one persona x
one angle, ranked best first by research evidence (strongest pains, verbatim language, white space vs
competitors). Use only persona refs from the research. Choose the funnel stage: TOF for a new brand or
small audience; MOF only if the brief says the site already has traffic. Keep names short. When the brief lists
several products, set product to the exact name of the one each ad set sells (best match for its persona and
angle); leave it empty for brand-level ad sets."""


def _slug(t: str, n: int = 12) -> str:
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:n].strip("_") or "x"


def _fallback(r: ResearchReport) -> _Plan:
    angles = r.recommended_angles or [d for p in r.personas for d in p.desires] or ["core benefit"]
    ideas = [AdSetIdea(persona_ref=p.ref, angle=_cut(re.split(r" — | / |: ", angles[i % len(angles)])[0], 40),
                       rationale="Top-ranked research angle for this persona.")
             for i, p in enumerate(r.personas[:MAX_ADSETS])]
    return _Plan(ideas=ideas, rationale="Fallback plan: one ad set per persona with the top research angles.")


def negotiate(cid: str, brief: ClientBrief, n: int) -> tuple[int, str]:
    """Ask Shlok's budget agent; if the budget can't carry n ad sets, accept the counter-proposal.
    Returns (n_adsets, note for the blueprint rationale)."""
    bus.log(cid, AGENT, f"Proposing {n} ad sets to shlok.budget")
    check = budget_feasibility(brief, n_adsets=n, asked_by=AGENT)
    if check.feasible:
        bus.log(cid, AGENT, f"Budget agreed: {check.suggestion}")
        return n, ""
    if check.max_adsets < 1:  # not even one ad set reaches the learning threshold: nothing left to haggle
        note = (f"Budget ({check.total_daily:,.0f}/day) is below one ad set's learning threshold "
                f"({check.min_daily_per_adset:,.0f}/day): 1 ad set, and shlok.budget should optimise for an "
                f"upstream event (landing page views / add to cart) until conversions accumulate.")
        bus.say(cid, AGENT, "shlok.budget", "RESPONSE", {"accept": True, "n_adsets": 1, "text":
                "Understood. Going with 1 ad set; please optimise for an upstream event. The other angles run as "
                "ads inside it."})
        bus.log(cid, AGENT, "Budget pushback: below one ad set's threshold → 1 ad set, upstream optimisation event")
        return 1, note
    new_n = min(check.max_adsets, n)
    bus.say(cid, AGENT, "shlok.budget", "RESPONSE", {
        "accept": True, "n_adsets": new_n,
        "text": f"Understood. Dropping to the top {new_n} ad set(s) by evidence and testing the other angles "
                f"as ads inside them."})
    bus.log(cid, AGENT, f"Budget pushback: {check.suggestion} → reducing to {new_n}")
    budget_feasibility(brief, n_adsets=new_n, asked_by=AGENT)  # confirm the counter-proposal on the bus
    return new_n, ""


def max_adsets_for(brief: ClientBrief) -> int:
    """More ad sets than the deliverables can fill (MIN_ADS each) would mean inventing ads the client didn't ask for."""
    return max(1, (brief.deliverables.n_images + brief.deliverables.n_videos) // MIN_ADS)


def _ads(adset_refs: list[str], brief: ClientBrief, assets: list[own.Asset] = ()) -> dict[str, list[AdPlan]]:
    """Spread exactly the requested image/video ads over ad sets so each mixes formats. With the client's own
    creatives (`assets`) every upload is one ad in its own ratio."""
    d, n = brief.deliverables, len(adset_refs)
    if assets:
        images = [[a.ratio] for a in assets if a.kind == "image"]
        videos = [[a.ratio] for a in assets if a.kind == "video"]
    else:
        images, videos = [list(d.ratios)] * d.n_images, [list(d.ratios)] * d.n_videos
    per: dict[str, list[tuple[str, list]]] = {r: [] for r in adset_refs}
    for i, ratios in enumerate(images):
        per[adset_refs[i % n]].append(("image", ratios))
    for j, ratios in enumerate(videos):  # offset so videos land beside images, not in their own ad set
        per[adset_refs[(j + len(images)) % n]].append(("video", ratios))
    if brief.creative_focus == "range" and not assets:
        # a range sells best swiped: each ad set's first image ad is a carousel (cover + one card per product),
        # in the feed ratios Meta allows for carousels; its other image ads are line-ups
        feed = [r for r in d.ratios if r in ("1:1", "4:5")] or ["1:1"]
        for fs in per.values():
            k = next((i for i, (f, _) in enumerate(fs) if f == "image"), None)
            # its only image ad stays a line-up if it must also cover 9:16 / 1.91:1, which carousels can't
            if k is not None and (sum(f == "image" for f, _ in fs) > 1 or set(d.ratios) <= set(feed)):
                fs[k] = ("carousel", feed)
    out = {}
    for ref, fs in per.items():
        fs = fs or [("image", list(d.ratios))]  # only when the brief asked for zero ads: an ad set can't be empty
        short = ref[3:]
        out[ref] = [AdPlan(ref=f"ad_{short}_{i}", hook_id=f"h_{short}_{i}", format=f, ratios=r)
                    for i, (f, r) in enumerate(fs, 1)]
    return out


def build_blueprint(cid: str, brief: ClientBrief, research: ResearchReport, plan: _Plan, n: int,
                    assets: list[own.Asset] = ()) -> CampaignBlueprint:
    personas = {p.ref: p for p in research.personas}
    ideas = [i for i in plan.ideas if i.persona_ref in personas] or _fallback(research).ideas
    ideas, dropped = [i.model_copy() for i in ideas[:n]], ideas[n:]
    while len(ideas) < n:  # more languages than ideas: the best ideas again, so every language gets an ad set
        ideas.append(ideas[len(ideas) % max(1, len(ideas))].model_copy())
    for d in dropped:  # promised in the negotiation: cut ad sets' angles live on as hooks inside the kept ones
        home = next((i for i in ideas if i.persona_ref == d.persona_ref), ideas[0])
        home.rationale = f"{home.rationale} Also test as hooks: {d.angle}.".strip()
    refs, adsets = [], []
    lal = "lookalike" in audiences.plan(brief)
    for k, idea in enumerate(ideas):
        ref = f"as_{_slug(idea.persona_ref.removeprefix('p_'), 8)}_{_slug(idea.angle, 12)}"
        while ref in refs:
            ref += "2"
        refs.append(ref + ("_lal" if lal and k == 0 else ""))  # targeting: the first ad set tests a lookalike
    ads = _ads(refs, brief, assets)
    names = [p.name for p in brief.products]
    shown = [p.name for p in brief.products if p.images] or names  # catalogs: rotate through what has photos
    for i, (ref, idea) in enumerate(zip(refs, ideas)):
        name = idea.name or f"{personas[idea.persona_ref].name} · {idea.angle}"
        product = idea.product if idea.product in names else (
            shown[i % len(shown)] if brief.creative_focus == "product" and len(names) > 1 else "")
        tongues = [x for x in brief.languages if x] or ["en"]  # several languages: ad sets take turns
        adsets.append(AdSetPlan(ref=ref, name=name[:60], persona_ref=idea.persona_ref, angle=idea.angle,
                                rationale=idea.rationale, ads=ads[ref], product=product,
                                language=tongues[i % len(tongues)] if len(tongues) > 1 else ""))
    stage = plan.funnel_stage
    camp = CampaignPlan(ref=f"c_{stage.lower()}_{brief.goal}", name=f"{brief.business_name} · {stage} · {brief.goal.title()}",
                        objective=GOAL_TO_OBJECTIVE[brief.goal], funnel_stage=stage, rationale=plan.rationale,
                        adsets=adsets)
    n_ads = sum(len(a.ads) for a in adsets)
    why = (f"One {stage} {camp.objective} campaign with {len(adsets)} ad set(s) (what the budget supports) and "
           f"{n_ads} ads covering {brief.deliverables.n_images} image + {brief.deliverables.n_videos} video deliverables "
           f"in {', '.join(brief.deliverables.ratios)}. {plan.rationale}")
    return CampaignBlueprint(client_id=cid, campaigns=[camp], rationale=why.strip())


def add_audience_sets(cid: str, brief: ClientBrief, bp: CampaignBlueprint) -> CampaignBlueprint:
    """Beyond prospecting, when the money and the connections allow: a retargeting ad set (site visitors + engagers,
    running the best ad set's creatives) and an Advantage+ catalog campaign over every product."""
    from shlok import catalog
    main, n = bp.campaigns[0], len(bp.adsets())
    extras = []
    if audiences.can_retarget(brief) and brief.goal in ("sales", "leads", "traffic", "engagement") and main.adsets:
        extras.append("retargeting")
    if catalog.can_run(brief):
        extras.append("catalog")
    while extras and not budget_feasibility(brief, n_adsets=n + len(extras), asked_by=AGENT).feasible:
        dropped = extras.pop()  # catalog first: retargeting usually returns more per rupee
        bus.log(cid, AGENT, f"Budget can't carry a {dropped} ad set as well; skipping it")
    out = bp.model_copy(deep=True)
    if "retargeting" in extras:
        src = main.adsets[0]
        out.campaigns[0].adsets.append(AdSetPlan(
            ref="as_retarget", name="Retargeting · visitors & engagers", persona_ref=src.persona_ref,
            angle="come back and buy", rationale="People who already showed interest convert cheapest.", ads=[],
            audience="retargeting", reuse_from=src.ref, language=src.language))
        bus.log(cid, AGENT, f"Added a retargeting ad set running {src.name}'s creatives on recent visitors & engagers")
    if "catalog" in extras:
        out.campaigns.append(CampaignPlan(
            ref="c_catalog", name=f"{brief.business_name} · Catalog", objective=GOAL_TO_OBJECTIVE[brief.goal],
            funnel_stage=main.funnel_stage, rationale="Advantage+ catalog: Meta matches each person with products.",
            adsets=[AdSetPlan(ref="as_catalog", name="Catalog · all products", persona_ref=main.adsets[0].persona_ref,
                              angle="the whole catalog", ads=[], audience="catalog")]))
        bus.log(cid, AGENT, f"Added an Advantage+ catalog campaign over {len(catalog.eligible(brief))} products")
    if extras:
        out.rationale += f" Plus {' and '.join(extras)}."
    return out


def strategy_step(state: RuneState) -> dict:
    cid = state["client_id"]
    brief = ClientBrief.model_validate(state["brief"])
    if demo_mode(state):
        bp = CampaignBlueprint.model_validate(fixture("blueprint", cid))
        check = budget_feasibility(brief, n_adsets=len(bp.adsets()), asked_by=AGENT)
        bus.log(cid, AGENT, f"Demo run: fixture blueprint; budget feasible={check.feasible}")
        return {"blueprint": bp.model_dump(mode="json")}

    research = ResearchReport.model_validate(state["research"])
    note = (state.get("notes") or {}).get("strategy", "")
    bus.log(cid, AGENT, "Designing campaign structure (skills: ads, marketing-plan)" + (f"; applying: {note}" if note else ""))
    try:
        user = (f"Products: {[p.name for p in brief.products]} (ads show: {brief.creative_focus})\n"
                f"Goal: {brief.goal}  Budget: {brief.budget.total:,.0f} {brief.budget.currency} "
                f"{brief.budget.start}→{brief.budget.end}\nPersonas: {[p.model_dump(include={'ref','name','pains','desires'}) for p in research.personas]}\n"
                f"Recommended angles: {research.recommended_angles}\nCompetitors: "
                f"{[(c.name, c.angles) for c in research.competitors]}\nSummary: {research.summary}")
        if note:
            user += f"\n\nHuman instruction for this rerun (apply it): {note}"
        plan = llm.structured("reason", skills.with_skills(SYSTEM, ["ads", "marketing-plan"], state.get("context_doc", "")),
                              user, _Plan)
        plan.ideas = [i for i in plan.ideas if i.persona_ref in {p.ref for p in research.personas}]
        if not plan.ideas:
            raise ValueError("no idea uses a known persona")
    except Exception as e:
        bus.log(cid, AGENT, f"LLM plan unavailable ({e.__class__.__name__}); one ad set per persona")
        plan = _fallback(research)

    want = min(len(plan.ideas) or 1, MAX_ADSETS, max_adsets_for(brief))
    tongues = [x for x in brief.languages if x]
    if len(tongues) > want:  # every language needs its own ad set (at least one ad each)
        want = min(len(tongues), MAX_ADSETS, max(1, brief.deliverables.n_images + brief.deliverables.n_videos))
    n, budget_note = negotiate(cid, brief, want)
    if len(tongues) > n:
        bus.log(cid, AGENT, f"Budget or deliverables allow {n} ad set(s): ads run in {', '.join(tongues[:n])} only")
    bp = build_blueprint(cid, brief, research, plan, n, own.probe_all(cid, own.uploads(state)))
    if budget_note:
        bp.rationale = f"{bp.rationale} {budget_note}"
    bp = add_audience_sets(cid, brief, bp)
    if moments := calendar.events_in(brief.budget.start, brief.budget.end, brief.geo):
        bp.rationale += f" Moments in this flight: {calendar.describe(moments)}."
        bus.log(cid, AGENT, f"Shopping moments in the flight: {calendar.describe(moments)}")
    for s in bp.adsets():
        bus.log(cid, AGENT, f"Ad set {s.name}: {len(s.ads)} ads ({', '.join(a.format for a in s.ads)})")
    return {"blueprint": bp.model_dump(mode="json")}
