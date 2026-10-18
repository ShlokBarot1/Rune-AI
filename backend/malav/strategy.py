"""M3 Strategy Agent: research -> CampaignBlueprint, negotiated with Shlok's budget agent.

The LLM ranks ad-set ideas (persona x angle) and picks the funnel stage; code builds the tree so
refs, deliverable counts and ratios are always right:
  * objective comes from GOAL_TO_OBJECTIVE
  * budget_feasibility() decides how many ad sets the money supports (visible negotiation on the bus)
  * ads = the client's image/video deliverables spread over the ad sets, at least 2 ads each
  * every ad gets a hook slot id (h_<adset>_<n>) that the hooks agent fills
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from core import bus, llm, skills
from core.schemas import (GOAL_TO_OBJECTIVE, AdPlan, AdSetPlan, CampaignBlueprint, CampaignPlan, ClientBrief,
                          FunnelStage, ResearchReport)
from core.state import RuneState
from malav.common import demo_mode, fixture
from shlok.budget import budget_feasibility

AGENT = "malav.strategy"
MAX_ADSETS = 4
MIN_ADS, MAX_ADS = 2, 6


class AdSetIdea(BaseModel):
    persona_ref: str
    angle: str           # short, e.g. "cafe price vs ₹55/cup"
    name: str = ""
    rationale: str = ""  # cite the research evidence


class _Plan(BaseModel):
    funnel_stage: FunnelStage = "TOF"
    ideas: list[AdSetIdea]   # best first
    rationale: str = ""


SYSTEM = """You are the strategy agent of a Meta ads agency. Propose 2-4 ad sets, each one persona x
one angle, ranked best first by research evidence (strongest pains, verbatim language, white space vs
competitors). Use only persona refs from the research. Choose the funnel stage: TOF for a new brand or
small audience; MOF only if the brief says the site already has traffic. Keep names short."""


def _slug(t: str, n: int = 12) -> str:
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:n].strip("_") or "x"


def _fallback(r: ResearchReport) -> _Plan:
    angles = r.recommended_angles or [d for p in r.personas for d in p.desires] or ["core benefit"]
    ideas = [AdSetIdea(persona_ref=p.ref, angle=angles[i % len(angles)].split(" — ")[0][:40],
                       rationale="Top-ranked research angle for this persona.")
             for i, p in enumerate(r.personas[:MAX_ADSETS])]
    return _Plan(ideas=ideas, rationale="Fallback plan: one ad set per persona with the top research angles.")


def negotiate(cid: str, brief: ClientBrief, n: int) -> int:
    """Ask Shlok's budget agent; if the budget can't carry n ad sets, accept the counter-proposal."""
    bus.log(cid, AGENT, f"Proposing {n} ad sets to shlok.budget")
    check = budget_feasibility(brief, n_adsets=n, asked_by=AGENT)
    if check.feasible:
        bus.log(cid, AGENT, f"Budget agreed: {check.suggestion}")
        return n
    new_n = max(1, min(check.max_adsets, n))
    bus.say(cid, AGENT, "shlok.budget", "RESPONSE", {
        "accept": True, "n_adsets": new_n,
        "text": f"Understood. Dropping to the top {new_n} ad set(s) by evidence and testing the other angles "
                f"as ads inside them."})
    bus.log(cid, AGENT, f"Budget pushback: {check.suggestion} → reducing to {new_n}")
    budget_feasibility(brief, n_adsets=new_n, asked_by=AGENT)  # confirm the counter-proposal on the bus
    return new_n


def _ads(adset_refs: list[str], brief: ClientBrief) -> dict[str, list[AdPlan]]:
    """Spread the requested image/video ads over ad sets so each mixes formats, then pad to MIN_ADS images."""
    d, n = brief.deliverables, len(adset_refs)
    per: dict[str, list[str]] = {r: [] for r in adset_refs}
    for i in range(d.n_images):
        per[adset_refs[i % n]].append("image")
    for j in range(d.n_videos):  # offset so videos land beside images, not in their own ad set
        per[adset_refs[(j + d.n_images) % n]].append("video")
    out = {}
    for ref, fs in per.items():
        fs = (fs + ["image"] * MIN_ADS)[:max(MIN_ADS, min(len(fs), MAX_ADS))]
        short = ref[3:]
        out[ref] = [AdPlan(ref=f"ad_{short}_{i}", hook_id=f"h_{short}_{i}", format=f, ratios=list(d.ratios))
                    for i, f in enumerate(fs, 1)]
    return out


def build_blueprint(cid: str, brief: ClientBrief, research: ResearchReport, plan: _Plan, n: int) -> CampaignBlueprint:
    personas = {p.ref: p for p in research.personas}
    ideas = [i for i in plan.ideas if i.persona_ref in personas] or _fallback(research).ideas
    ideas, dropped = [i.model_copy() for i in ideas[:n]], ideas[n:]
    for d in dropped:  # promised in the negotiation: cut ad sets' angles live on as hooks inside the kept ones
        home = next((i for i in ideas if i.persona_ref == d.persona_ref), ideas[0])
        home.rationale = f"{home.rationale} Also test as hooks: {d.angle}.".strip()
    refs, adsets = [], []
    for idea in ideas:
        ref = f"as_{_slug(idea.persona_ref.removeprefix('p_'), 8)}_{_slug(idea.angle, 12)}"
        while ref in refs:
            ref += "2"
        refs.append(ref)
    ads = _ads(refs, brief)
    for ref, idea in zip(refs, ideas):
        name = idea.name or f"{personas[idea.persona_ref].name} · {idea.angle}"
        adsets.append(AdSetPlan(ref=ref, name=name[:60], persona_ref=idea.persona_ref, angle=idea.angle,
                                rationale=idea.rationale, ads=ads[ref]))
    stage = plan.funnel_stage
    camp = CampaignPlan(ref=f"c_{stage.lower()}_{brief.goal}", name=f"{brief.business_name} · {stage} · {brief.goal.title()}",
                        objective=GOAL_TO_OBJECTIVE[brief.goal], funnel_stage=stage, rationale=plan.rationale,
                        adsets=adsets)
    n_ads = sum(len(a.ads) for a in adsets)
    why = (f"One {stage} {camp.objective} campaign with {len(adsets)} ad set(s) (what the budget supports) and "
           f"{n_ads} ads covering {brief.deliverables.n_images} image + {brief.deliverables.n_videos} video deliverables "
           f"in {', '.join(brief.deliverables.ratios)}. {plan.rationale}")
    return CampaignBlueprint(client_id=cid, campaigns=[camp], rationale=why.strip())


def strategy_step(state: RuneState) -> dict:
    cid = state["client_id"]
    brief = ClientBrief.model_validate(state["brief"])
    if demo_mode(state):
        bp = CampaignBlueprint.model_validate(fixture("blueprint"))
        check = budget_feasibility(brief, n_adsets=len(bp.adsets()), asked_by=AGENT)
        bus.log(cid, AGENT, f"Demo run: fixture blueprint; budget feasible={check.feasible}")
        return {"blueprint": bp.model_dump(mode="json")}

    research = ResearchReport.model_validate(state["research"])
    note = (state.get("notes") or {}).get("strategy", "")
    bus.log(cid, AGENT, "Designing campaign structure (skills: ads, marketing-plan)" + (f"; applying: {note}" if note else ""))
    try:
        user = (f"Goal: {brief.goal}  Budget: {brief.budget.total:,.0f} {brief.budget.currency} "
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

    n = negotiate(cid, brief, min(len(plan.ideas) or 1, MAX_ADSETS))
    bp = build_blueprint(cid, brief, research, plan, n)
    for s in bp.adsets():
        bus.log(cid, AGENT, f"Ad set {s.name}: {len(s.ads)} ads ({', '.join(a.format for a in s.ads)})")
    return {"blueprint": bp.model_dump(mode="json")}
