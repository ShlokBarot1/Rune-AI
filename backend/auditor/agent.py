"""Auditor: deterministic checks in code + LLM policy/quality review. Owned by both.

Decision: FAIL on any blocker or mean score < 70; PASS_WITH_NOTES if any finding; else PASS.
Every finding names its owner so the fix prompt goes to the right agent.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from core import bus, llm, skills
from core.schemas import (RATIO_SIZES, AuditReport, BudgetPlan, ClientBrief, CreativePackage,
                          Finding, FixPrompt, LaunchResult, TargetingSpec)
from core.state import RuneState

DIMENSIONS = ["brief", "strategy", "creative", "copy_policy", "budget", "targeting", "technical"]
PENALTY = {"blocker": 40, "major": 15, "minor": 5}


def deterministic_checks(state: RuneState) -> list[Finding]:
    brief = ClientBrief.model_validate(state["brief"])
    pkg = CreativePackage.model_validate(state["package"])
    plan = BudgetPlan.model_validate(state["budget_plan"])
    targeting = {t.adset_ref: t for t in map(TargetingSpec.model_validate, state.get("targeting", []))}
    launch = LaunchResult.model_validate(state.get("launch") or {"dry_run": True})
    out: list[Finding] = []
    add = lambda sev, dim, ref, owner, msg: out.append(Finding(severity=sev, dimension=dim, object_ref=ref, owner=owner, message=msg))

    # brief: deliverables honoured
    images = [c for c in pkg.creatives if c.kind == "image"]
    videos = [c for c in pkg.creatives if c.kind == "video"]
    if len({c.ad_ref for c in images}) < brief.deliverables.n_images:
        add("major", "brief", "package", "malav", f"Client asked for {brief.deliverables.n_images} image ads, package has {len({c.ad_ref for c in images})}.")
    if len({c.ad_ref for c in videos}) < brief.deliverables.n_videos:
        add("major", "brief", "package", "malav", f"Client asked for {brief.deliverables.n_videos} video ads, package has {len({c.ad_ref for c in videos})}.")
    for r in set(brief.deliverables.ratios) - {c.ratio for c in pkg.creatives}:
        add("major", "brief", "package", "malav", f"Requested ratio {r} missing from every creative.")

    # creative + copy
    for c in pkg.creatives:
        if (c.width, c.height) != RATIO_SIZES[c.ratio]:
            add("major", "creative", c.creative_id, "malav", f"{c.ratio} must be {RATIO_SIZES[c.ratio]}, got {(c.width, c.height)}.")
        if not Path(c.path).exists():
            add("blocker", "creative", c.creative_id, "malav", f"Asset file missing: {c.path}")
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
        return llm.structured("reason", system, user, _LLMFindings, agent="auditor").findings
    except Exception as e:  # free models fail sometimes; deterministic checks still gate go-live
        bus.log(state["client_id"], "auditor", f"LLM review skipped ({e.__class__.__name__})")
        return []


def audit(state: RuneState) -> AuditReport:
    cid = state["client_id"]
    bus.log(cid, "auditor", "Auditing brief, creatives, budget, targeting and Meta build")
    findings = deterministic_checks(state) + llm_review(state)
    scores = {d: 100 for d in DIMENSIONS}
    for f in findings:
        scores[f.dimension] = max(0, scores.get(f.dimension, 100) - PENALTY[f.severity])
    mean = sum(scores.values()) / len(scores)
    decision = ("FAIL" if any(f.severity == "blocker" for f in findings) or mean < 70
                else "PASS_WITH_NOTES" if findings else "PASS")

    by_owner: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.severity != "minor" or decision == "FAIL":
            by_owner[f.owner].append(f)
    fixes = [FixPrompt(owner=o, object_refs=[f.object_ref for f in fs],
                       prompt="Fix these audit findings:\n" + "\n".join(f"- [{f.severity}] {f.object_ref}: {f.message}" for f in fs))
             for o, fs in by_owner.items()]
    report = AuditReport(decision=decision, scores=scores, findings=findings, fix_prompts=fixes)
    bus.say(cid, "auditor", "human", "INFORM", {"decision": decision, "scores": scores, "n_findings": len(findings)})
    return report
