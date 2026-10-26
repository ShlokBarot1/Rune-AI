"""Auditor: deterministic checks in code + LLM policy/quality review. Owned by both.

Decision: FAIL on any blocker or mean score < 70; PASS_WITH_NOTES if any finding; else PASS.
Every finding names its owner so the fix prompt goes to the right agent.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from core import bus, compliance, llm, skills
from core.schemas import (RATIO_SIZES, AuditReport, BudgetPlan, ClientBrief, CreativePackage,
                          Finding, FixPrompt, LaunchResult, TargetingSpec)
from core.state import RuneState

DIMENSIONS = ["brief", "strategy", "creative", "copy_policy", "budget", "targeting", "technical"]
PENALTY = {"blocker": 40, "major": 15, "minor": 5}
BACKEND = Path(__file__).resolve().parent.parent  # creative paths are relative to backend/, whatever the cwd


def deterministic_checks(state: RuneState) -> list[Finding]:
    brief = ClientBrief.model_validate(state["brief"])
    pkg = CreativePackage.model_validate(state["package"])
    plan = BudgetPlan.model_validate(state["budget_plan"])
    targeting = {t.adset_ref: t for t in map(TargetingSpec.model_validate, state.get("targeting", []))}
    launch = LaunchResult.model_validate(state.get("launch") or {"dry_run": True})
    out: list[Finding] = []
    add = lambda sev, dim, ref, owner, msg: out.append(Finding(severity=sev, dimension=dim, object_ref=ref, owner=owner, message=msg))

    # brief: deliverables honoured
    images = [c for c in pkg.creatives if c.kind in ("image", "carousel")]  # a carousel is an image ad
    videos = [c for c in pkg.creatives if c.kind == "video"]
    if len({c.ad_ref for c in images}) < brief.deliverables.n_images:
        add("major", "brief", "package", "malav", f"Client asked for {brief.deliverables.n_images} image ads, package has {len({c.ad_ref for c in images})}.")
    if len({c.ad_ref for c in videos}) < brief.deliverables.n_videos:
        add("major", "brief", "package", "malav", f"Client asked for {brief.deliverables.n_videos} video ads, package has {len({c.ad_ref for c in videos})}.")
    for r in set(brief.deliverables.ratios) - {c.ratio for c in pkg.creatives}:
        add("major", "brief", "package", "malav", f"Requested ratio {r} missing from every creative.")

    # creative + copy
    for c in pkg.creatives:
        rw, rh = RATIO_SIZES[c.ratio]  # client uploads keep their own resolution; only the shape must match
        if not c.height or abs(c.width / c.height / (rw / rh) - 1) > 0.03:
            add("major", "creative", c.creative_id, "malav", f"{c.ratio} must be {rw}x{rh}-shaped, got {(c.width, c.height)}.")
        if not (BACKEND / c.path).exists():
            add("blocker", "creative", c.creative_id, "malav", f"Asset file missing: {c.path}")
        if c.kind == "carousel" and not 2 <= len(c.cards) <= 10:
            add("blocker", "creative", c.creative_id, "malav", f"A carousel needs 2-10 cards, has {len(c.cards)}.")
        for card in c.cards:
            if not (BACKEND / card.path).exists():
                add("blocker", "creative", c.creative_id, "malav", f"Carousel card missing: {card.path}")
        if not c.qa.passed:
            add("major", "creative", c.creative_id, "malav", f"Creative QA did not pass: {c.qa.notes}")
        if len(c.ad_copy.headline) > 40:
            add("minor", "copy_policy", c.creative_id, "malav", "Headline over 40 characters; it will truncate.")
        if len(c.ad_copy.primary_text) > 300:
            add("minor", "copy_policy", c.creative_id, "malav", "Primary text very long; first 125 characters must carry the hook.")
        if not c.landing_url.startswith("https://") or "utm_" not in c.landing_url:
            add("major", "technical", c.creative_id, "malav", "Landing URL must be https and carry UTM parameters.")

    # strategy: every planned ad has creatives
    for cmp in pkg.blueprint.campaigns:
        for s in cmp.adsets:
            for ad in s.ads:
                if not pkg.for_ad(ad.ref):
                    add("blocker", "strategy", ad.ref, "malav", "Planned ad has no creative.")

    # budget
    flight_total = sum(plan.adset_daily.values()) * plan.days + plan.testing_reserve * plan.days
    if flight_total > brief.budget.total * (1 + brief.budget.flexibility_pct / 100) + 0.01:
        add("blocker", "budget", "budget_plan", "shlok", f"Planned spend {flight_total:.2f} exceeds client budget {brief.budget.total:.2f}.")
    for s in pkg.blueprint.adsets():
        if plan.adset_daily.get(s.ref, 0) <= 0:
            add("blocker", "budget", s.ref, "shlok", "Ad set has no budget.")
        if s.ref not in targeting:
            add("blocker", "targeting", s.ref, "shlok", "Ad set has no targeting spec.")

    # copy may only state facts the brief backs (no invented prices, quantities, comparisons)
    from malav.hooks import facts_of, ungrounded
    facts = facts_of(brief, state.get("context_doc", ""))
    for c in pkg.creatives:
        if made_up := ungrounded(f"{c.ad_copy.primary_text} {c.ad_copy.headline} {c.ad_copy.description}", facts):
            add("major", "copy_policy", c.creative_id, "malav", "Unsupported claim: " + "; ".join(made_up[:3]))

    # policy: restricted categories (core.compliance)
    for r in compliance.rules_for(brief):
        if r.action == "blocked":
            add("blocker", "copy_policy", "brief", "human", f"Meta doesn't allow ads for {r.category}. {r.copy_rules}")
        elif r.action == "authorization" and r.category not in brief.authorized:
            add("blocker", "copy_policy", "brief", "human", f"{r.category} needs Meta's written authorisation: get it, then "
                                                           "tick it under Brief → Restricted categories.")
    # offer + local
    if brief.offer and brief.offer.ends:
        if brief.offer.ends < brief.budget.start:
            add("major", "brief", "brief", "human", f"The offer ends {brief.offer.ends}, before the ads start "
                                                    f"({brief.budget.start}). Update or remove it.")
        elif brief.offer.ends < brief.budget.end:
            add("minor", "brief", "brief", "shlok", f"The offer ends {brief.offer.ends}: its ads pause automatically then.")
    for loc in brief.locations:
        if loc.lat is None and not loc.pincodes and not loc.city:
            add("major", "targeting", "brief", "human", f"{loc.name}: no address, city or postcode to target. Add one.")

    # technical: everything built and paused before go-live
    built = {o.local_ref for o in launch.objects}
    for c in pkg.creatives:
        if f"ad:{c.creative_id}" not in built:
            add("blocker", "technical", c.creative_id, "shlok", "Creative not built as a Meta ad.")
    for o in launch.objects:
        if o.kind in ("campaign", "adset", "ad") and o.status != "PAUSED" and not state.get("live"):
            add("blocker", "technical", o.local_ref, "shlok", f"{o.kind} is {o.status} before go-live approval.")
    return out


class _LLMFindings(BaseModel):
    findings: list[Finding]


POLICY_SYSTEM = """You are the final auditor of an AI ad agency. Review the ad copy against Meta
Advertising Standards (no personal-attribute assertions like "Are you overweight?", no unrealistic or
guaranteed claims, careful before/after, special ad category wording) and against the client brief
(tone, constraints, don't-words). Also judge hook strength and persona fit. Only report real problems.
owner is "malav" for creative/copy problems, "shlok" for budget/targeting problems."""


def llm_review(state: RuneState) -> list[Finding]:
    pkg = CreativePackage.model_validate(state["package"])
    copy = [{"creative_id": c.creative_id, "adset": c.adset_ref, **c.ad_copy.model_dump()} for c in pkg.creatives]
    user = f"Brief:\n{json.dumps(state['brief'], default=str)[:5000]}\n\nAds:\n{json.dumps(copy)[:10000]}"
    try:
        system = skills.with_skills(POLICY_SYSTEM, ["ad-creative", "copy-editing"])
        found = llm.structured("reason", system, user, _LLMFindings, agent="auditor").findings
        # the model names its own categories; scores stay on the fixed rubric (this review is about copy + policy)
        return [f.model_copy(update={"dimension": f.dimension if f.dimension in DIMENSIONS else "copy_policy"}) for f in found]
    except Exception as e:  # free models fail sometimes; deterministic checks still gate go-live
        bus.log(state["client_id"], "auditor", f"LLM review skipped ({e.__class__.__name__})")
        return []


def refs(object_ref: str) -> list[str]:
    """The ids in a finding's ref. The LLM review writes "cr_a, cr_b" or "cr_a – primary_text"; fixers match exact ids."""
    return re.findall(r"\b(?:cr|ad|as|c)_\w+", object_ref) or [object_ref.strip()]


def audit(state: RuneState) -> AuditReport:
    cid = state["client_id"]
    bus.log(cid, "auditor", "Auditing brief, creatives, budget, targeting and Meta build")
    findings = deterministic_checks(state) + llm_review(state)
    scores = {d: 100 for d in DIMENSIONS}
    for f in findings:
        scores[f.dimension] = max(0, scores.get(f.dimension, 100) - PENALTY[f.severity])
    mean = sum(scores.values()) / len(scores)
    # one collapsed dimension (e.g. every creative failing QA) must not be averaged away by the others
    decision = ("FAIL" if any(f.severity == "blocker" for f in findings) or mean < 70 or min(scores.values()) < 50
                else "PASS_WITH_NOTES" if findings else "PASS")

    by_owner: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:  # "human" findings (authorisation, an offer that's over) go to the go-live card, not to agents
        if f.owner != "human" and (f.severity != "minor" or decision == "FAIL"):
            by_owner[f.owner].append(f)
    fixes = [FixPrompt(owner=o, object_refs=[r for f in fs for r in refs(f.object_ref)],
                       prompt="Fix these audit findings:\n" + "\n".join(f"- [{f.severity}] {f.object_ref}: {f.message}" for f in fs))
             for o, fs in by_owner.items()]
    report = AuditReport(decision=decision, scores=scores, findings=findings, fix_prompts=fixes)
    bus.say(cid, "auditor", "human", "INFORM", {"decision": decision, "scores": scores, "n_findings": len(findings)})
    return report
