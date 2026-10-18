"""S3 Targeting & Placement Agent: persona -> Meta targeting spec.

Deterministic mapping; interests resolved to real Meta IDs via the Targeting Search API when a token
exists. A human change request is turned into a small structured patch by the LLM, then applied in code.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from core import bus, llm
from core.schemas import AdSetPlan, ClientBrief, CreativePackage, Persona, ResearchReport, TargetingSpec

from . import meta

GENDERS = {"male": [1], "female": [2]}


class TargetingPatch(BaseModel):
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    genders: Optional[list[Literal["male", "female", "all"]]] = None
    add_interests: list[str] = []
    remove_interests: list[str] = []
    countries: Optional[list[str]] = None


def _patch_from_note(client_id: str, note: str, current: dict) -> TargetingPatch:
    try:
        return llm.structured("fast", "Convert the request into a targeting patch. Leave every field the request does "
                                      "not mention null or empty; never invent interests.",
                              f"Current: {current}\nRequest: {note}", TargetingPatch, agent="shlok.targeting")
    except Exception as e:
        bus.log(client_id, "shlok.targeting", f"could not parse targeting request ({e.__class__.__name__})")
        return TargetingPatch()


def build_spec(brief: ClientBrief, adset: AdSetPlan, persona: Optional[Persona], ratios: set[str],
               patch: Optional[TargetingPatch] = None) -> TargetingSpec:
    patch = patch or TargetingPatch()
    age_min, age_max = persona.age_range if persona else (18, 65)
    genders = persona.genders if persona else ["all"]
    interests = [i for i in (persona.interests if persona else []) if i not in patch.remove_interests] + patch.add_interests
    countries = patch.countries or brief.geo
    age_min, age_max = patch.age_min or age_min, patch.age_max or age_max
    genders = patch.genders or genders

    restricted = brief.special_ad_category != "none"
    if restricted:  # housing/credit/employment/social issues: no age/gender narrowing, no detailed targeting
        age_min, age_max, genders, interests = 18, 65, ["all"], []

    spec: dict = {
        "geo_locations": {"countries": countries},
        "age_min": max(18, age_min), "age_max": min(65, age_max),
        "targeting_automation": {"advantage_audience": 0},
    }
    g = sorted({x for gname in genders for x in GENDERS.get(gname, [])})
    if g and len(g) < 2:
        spec["genders"] = g

    resolved: list[dict] = []
    if interests and not meta.dry_run():
        for q in interests[:6]:
            try:
                resolved += meta.search_interests(q, limit=1)
            except meta.MetaError as e:
                bus.log(brief.client_id, "shlok.targeting", f"interest lookup failed for '{q}': {e}")
    if resolved:
        spec["flexible_spec"] = [{"interests": resolved}]

    if "9:16" not in ratios:  # no vertical asset -> keep out of Stories/Reels
        spec |= {"publisher_platforms": ["facebook", "instagram"],
                 "facebook_positions": ["feed"], "instagram_positions": ["stream"]}
        placements = "Feeds only (no 9:16 asset)"
    else:
        placements = "Advantage+ placements"

    who = "everyone" if not spec.get("genders") else ("men" if spec["genders"] == [1] else "women")
    interest_txt = (", interests: " + ", ".join(i["name"] for i in resolved)) if resolved else \
        (f", interests to resolve: {', '.join(interests)}" if interests else ", broad")
    summary = (f"{who.capitalize()} {spec['age_min']}-{spec['age_max']} in {', '.join(countries)}{interest_txt}. "
               f"{placements}." + (" Special ad category rules applied." if restricted else ""))
    return TargetingSpec(adset_ref=adset.ref, spec=spec, summary=summary)


def plan_targeting(brief: ClientBrief, research: Optional[ResearchReport], pkg: CreativePackage,
                   note: Optional[str] = None) -> list[TargetingSpec]:
    personas = {p.ref: p for p in (research.personas if research else [])}
    out = []
    for s in pkg.blueprint.adsets():
        ratios = {c.ratio for c in pkg.creatives if c.adset_ref == s.ref}
        patch = _patch_from_note(brief.client_id, note, {"adset": s.name}) if note else None
        t = build_spec(brief, s, personas.get(s.persona_ref), ratios, patch)
        bus.log(brief.client_id, "shlok.targeting", f"{s.name}: {t.summary}")
        out.append(t)
    return out
