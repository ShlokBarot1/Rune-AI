"""S3 Targeting & Placement Agent: persona -> Meta targeting spec.

Deterministic mapping; interests resolved to real Meta IDs via the Targeting Search API when a token
exists. A human change request is turned into a small structured patch by the LLM, then applied in code.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from core import bus, compliance, langs, llm
from core.schemas import AdSetPlan, ClientBrief, CreativePackage, Persona, ResearchReport, TargetingSpec

from . import audiences, meta

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


SPECIAL_MIN_RADIUS_KM = 25    # Meta: special ad categories need >= 15 miles around a point, and no postcodes


def _where(brief: ClientBrief, countries: list[str]) -> tuple[dict, str]:
    """geo_locations + a phrase: around the business's locations when it has them, else whole countries."""
    if not brief.locations:
        return {"countries": countries}, f"in {', '.join(countries)}"
    special = brief.special_ad_category != "none"
    pins, zips, cities = [], [], []
    for loc in brief.locations:
        cc = (loc.country or brief.geo[0]).upper()
        if loc.lat is not None and loc.lng is not None:
            km = max(loc.radius_km, SPECIAL_MIN_RADIUS_KM) if special else min(max(loc.radius_km, 1), 80)
            pins.append({"latitude": loc.lat, "longitude": loc.lng, "radius": round(km, 1), "distance_unit": "kilometer",
                         "name": loc.name})
        if loc.pincodes and not special:
            zips += [{"key": f"{cc}:{z}"} for z in loc.pincodes]
        if loc.lat is None and not loc.pincodes and loc.city:
            cities.append((cc, loc.city))
    geo: dict = {"location_types": ["home", "recent"]}  # lives or was recently there: walk-in customers
    if pins:
        geo["custom_locations"] = pins
    if zips:
        geo["zips"] = zips
    for cc, city in cities:  # a city without a map pin: Meta's city key (live only; otherwise the country)
        try:
            hit = [] if meta.dry_run() else meta.api("GET", "search", params={
                "type": "adgeolocation", "location_types": '["city"]', "q": city, "country_code": cc, "limit": 1}).get("data", [])
            if hit:
                geo.setdefault("cities", []).append({"key": hit[0]["key"], "radius": 10, "distance_unit": "kilometer"})
        except meta.MetaError:
            pass
    if not (pins or zips or geo.get("cities")):
        return {"countries": countries}, f"in {', '.join(countries)} (locations couldn't be placed on the map)"
    where = ", ".join(f"{p['radius']:g} km around {p['name']}" for p in pins)
    where += (", " if where and zips else "") + (f"{len(zips)} postcodes" if zips else "")
    where += (", " if where and geo.get("cities") else "") + ", ".join(c for _, c in cities if geo.get("cities"))
    return geo, where


def locale_ids(language: str) -> list[int]:
    """Meta locale ids for a language (live only: dry-run targets everyone and says so)."""
    if meta.dry_run() or not language:
        return []
    try:
        data = meta.api("GET", "search", params={"type": "adlocale", "q": langs.name(language), "limit": 5}).get("data", [])
        return [d["key"] for d in data]
    except meta.MetaError:
        return []


def build_spec(brief: ClientBrief, adset: AdSetPlan, persona: Optional[Persona], ratios: set[str],
               patch: Optional[TargetingPatch] = None, audience_ids: Optional[dict[str, str]] = None) -> TargetingSpec:
    patch, aud = patch or TargetingPatch(), audience_ids or {}
    age_min, age_max = persona.age_range if persona else (18, 65)
    genders = persona.genders if persona else ["all"]
    interests = [i for i in (persona.interests if persona else []) if i not in patch.remove_interests] + patch.add_interests
    countries = patch.countries or brief.geo
    age_min, age_max = patch.age_min or age_min, patch.age_max or age_max
    genders = patch.genders or genders

    restricted = brief.special_ad_category != "none"
    if restricted:  # housing/credit/employment/social issues: no age/gender narrowing, no detailed targeting
        age_min, age_max, genders, interests = 18, 65, ["all"], []
    floor = compliance.min_age(brief)  # alcohol, gambling, weight loss...: never below the category's age
    if adset.audience != "prospecting":
        interests = []  # retargeting / catalog: the audience is the people, not their interests

    geo, where = _where(brief, countries)
    spec: dict = {
        "geo_locations": geo,
        "age_min": max(floor, age_min), "age_max": max(min(65, age_max), max(floor, age_min)),
        "targeting_automation": {"advantage_audience": 0},
    }
    g = sorted({x for gname in genders for x in GENDERS.get(gname, [])})
    if g and len(g) < 2:
        spec["genders"] = g

    who_extra = ""
    exclude = [aud[k] for k in ("purchasers", "customers") if k in aud]
    if adset.audience == "retargeting":
        include = [aud[k] for k in ("visitors", "engagers") if k in aud]
        spec["custom_audiences"] = [{"id": i} for i in include]
        who_extra = " who visited the site or engaged with the brand recently"
    elif adset.audience == "prospecting" and adset.ref.endswith("_lal") and "lookalike" in aud:
        spec["custom_audiences"] = [{"id": aud["lookalike"]}]
        interests, who_extra = [], f" similar to existing customers ({brief.audiences.lookalike_pct}% lookalike)"
    if exclude and adset.audience != "catalog":
        spec["excluded_custom_audiences"] = [{"id": i} for i in exclude]
        who_extra += ", excluding existing customers"

    language = adset.language or ""
    # several languages: each prospecting ad set reaches the people who use its language (retargeting reaches every
    # visitor, whatever their language)
    if language and len(brief.languages) > 1 and adset.audience == "prospecting" and (ids := locale_ids(language)):
        spec["locales"] = ids  # several languages: each ad set reaches the people who use its language

    resolved: list[dict] = []
    if interests and not meta.dry_run():
        for q in interests[:6]:
            try:
                resolved += meta.search_interests(q, limit=1)
            except meta.MetaError as e:
                bus.log(brief.client_id, "shlok.targeting", f"interest lookup failed for '{q}': {e}")
    if resolved:
        spec["flexible_spec"] = [{"interests": resolved}]

    if "9:16" not in ratios and adset.audience == "prospecting":  # no vertical asset -> keep out of Stories/Reels
        spec |= {"publisher_platforms": ["facebook", "instagram"],
                 "facebook_positions": ["feed"], "instagram_positions": ["stream"]}
        placements = "Feeds only (no 9:16 asset)"
    else:
        placements = "Advantage+ placements"

    who = "everyone" if not spec.get("genders") else ("men" if spec["genders"] == [1] else "women")
    interest_txt = (", interests: " + ", ".join(i["name"] for i in resolved)) if resolved else \
        (f", interests to resolve: {', '.join(interests)}" if interests else "")
    lang_txt = f", in {langs.name(language)}" if language and len(brief.languages) > 1 and adset.audience == "prospecting" else ""
    summary = (f"{who.capitalize()} {spec['age_min']}-{spec['age_max']} {where}{who_extra}{interest_txt}{lang_txt}. "
               f"{placements}." + (" Special ad category rules applied." if restricted else "")
               + (f" Age {floor}+ for {', '.join(brief.restricted)}." if floor > 18 else ""))
    return TargetingSpec(adset_ref=adset.ref, spec=spec, summary=summary)


def plan_targeting(brief: ClientBrief, research: Optional[ResearchReport], pkg: CreativePackage,
                   note: Optional[str] = None) -> list[TargetingSpec]:
    personas = {p.ref: p for p in (research.personas if research else [])}
    aud = audiences.ensure(brief)
    bus.log(brief.client_id, "shlok.targeting", audiences.describe(brief))
    out = []
    for s in pkg.blueprint.adsets():
        src = s.reuse_from or s.ref
        ratios = {c.ratio for c in pkg.creatives if c.adset_ref == src}
        patch = _patch_from_note(brief.client_id, note, {"adset": s.name}) if note else None
        t = build_spec(brief, s, personas.get(s.persona_ref), ratios, patch, aud)
        bus.log(brief.client_id, "shlok.targeting", f"{s.name}: {t.summary}")
        out.append(t)
    return out
