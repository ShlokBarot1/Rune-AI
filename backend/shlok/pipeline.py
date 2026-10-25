"""SHLOK'S PIPELINE: budget -> targeting -> Meta build (PAUSED). Activation happens after go-live.

Contract with the rest of the system:
  * STEPS: list[Step]; reads state["brief"], state["package"]; writes budget_plan, targeting, launch
  * apply_fixes(state, prompts) -> dict   (called every audit fix round, after Malav's fixes)
  * activate(state) -> dict               (after human go-live)
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from core import bus, llm
from core.approvals import ask_human
from core.schemas import (BudgetPlan, ClientBrief, CreativePackage, FixPrompt, LaunchResult, ResearchReport,
                          TargetingSpec)
from core.state import RuneState, Step

from . import budget, launch, targeting


class _Weights(BaseModel):
    adset_weights: dict[str, float]


def _weights_from_note(state: RuneState, note: str) -> dict[str, float] | None:
    """Turn a human budget request ("more on fitness") into ad-set weights; code then does the math."""
    pkg = CreativePackage.model_validate(state["package"])
    adsets = {s.ref: s.name for s in pkg.blueprint.adsets()}
    example = {"adset_weights": {ref: 1.0 for ref in adsets}}
    try:
        return llm.structured(
            "reason",
            "Turn the client's budget request into relative weights per ad set. Use exactly the given ad set refs as "
            "keys. 1.0 = an equal share; 2.0 = twice as much as a 1.0 ad set; 0 = no budget. "
            f"Example of the shape: {json.dumps(example)}",
            f"Ad sets (ref: name): {adsets}\nRequest: {note}", _Weights, agent="shlok.budget").adset_weights
    except Exception as e:
        bus.log(state["client_id"], "shlok.budget", f"could not parse budget request ({e.__class__.__name__}); keeping equal split")
        return None


FEE_WORDS = ("gst", "tax", "fee", "agency", "commission", "inclusive")


def budget_step(state: RuneState) -> dict:
    """S1 budget intake (clarify with the human) + S2 allocation."""
    brief = ClientBrief.model_validate(state["brief"])
    pkg = CreativePackage.model_validate(state["package"])
    cid, b = brief.client_id, brief.budget
    note = (state.get("notes") or {}).get("budget")
    prev = state.get("budget_plan") or {}
    net_total = prev.get("net_total")
    target_cpa = prev.get("target_cpa") or brief.kpi_targets.target_cpa

    if not note:  # first pass: clear up what the proposal leaves ambiguous (questions must come first in the node)
        if any(w in b.notes.lower() for w in FEE_WORDS):
            a = ask_human(cid, "shlok.budget",
                          f"The proposal mentions taxes/fees (\"{b.notes[:120]}\"). Is {b.total:,.0f} {b.currency} pure ad spend?",
                          ["Yes, all of it is ad spend", "No, it includes 18% GST"])
            net_total = round(b.total / 1.18, 2) if "18%" in a else None
        price = brief.products[0].price_in(b.currency) if brief.products else None
        past = ((state.get("research") or {}).get("benchmarks") or {}).get("cpa")
        if not target_cpa and brief.goal in ("sales", "leads") and past:  # the account's own history beats a rule
            a = ask_human(cid, "shlok.budget",
                          f"Your ad account's past cost per result is {past:,.0f} {b.currency}. Use it as the target CPA?",
                          [f"Use {round(past)}", "No CPA target"])
            target_cpa = round(past) if a.startswith("Use") else None
        elif not target_cpa and brief.goal in ("sales", "leads") and price:
            est = round(price * 0.35)
            a = ask_human(cid, "shlok.budget",
                          f"No target CPA was given. Use {est:,} {b.currency} (≈35% of the {price:,.0f} product price)?",
                          [f"Use {est}", "No CPA target"])
            target_cpa = est if a.startswith("Use") else None

    effective = brief.model_copy(update={
        "budget": b.model_copy(update={"total": net_total or b.total}),
        "kpi_targets": brief.kpi_targets.model_copy(update={"target_cpa": target_cpa}),
    })
    weights = _weights_from_note(state, note) if note else None
    try:
        plan = budget.allocate(effective, pkg.blueprint, weights)
    except ValueError as e:
        if not weights:
            raise
        # e.g. an audit note to "consolidate" zeroes an ad set the blueprint still has: never crash the run on it
        bus.log(cid, "shlok.budget", f"Couldn't apply that budget change ({e}). Keeping an equal split; dropping an "
                                     "ad set is a strategy change, so request it at the Strategy step")
        plan = budget.allocate(effective, pkg.blueprint)
    plan.total, plan.target_cpa, plan.net_total = b.total, target_cpa, net_total
    if net_total:
        plan.rationale = f"Ad spend {net_total:,.0f} after 18% GST. " + plan.rationale
    bus.log(cid, "shlok.budget", plan.rationale, adset_daily=plan.adset_daily)
    return {"budget_plan": plan.model_dump(mode="json")}


def targeting_step(state: RuneState) -> dict:
    brief = ClientBrief.model_validate(state["brief"])
    research = ResearchReport.model_validate(state["research"]) if state.get("research") else None
    pkg = CreativePackage.model_validate(state["package"])
    specs = targeting.plan_targeting(brief, research, pkg, (state.get("notes") or {}).get("targeting"))
    return {"targeting": [t.model_dump(mode="json") for t in specs]}


def launch_step(state: RuneState) -> dict:
    result = launch.build(
        ClientBrief.model_validate(state["brief"]), CreativePackage.model_validate(state["package"]),
        BudgetPlan.model_validate(state["budget_plan"]), [TargetingSpec.model_validate(t) for t in state["targeting"]])
    return {"launch": result.model_dump(mode="json")}


STEPS = [
    Step("budget", budget_step, ("budget_plan",)),
    Step("targeting", targeting_step, ("targeting",)),
    Step("launch", launch_step, ("launch",), gate=False),  # the Auditor + go-live gate review the build
]


def apply_fixes(state: RuneState, prompts: list[FixPrompt]) -> dict:
    """Re-plan budget if asked, then always re-sync the Meta build (Malav may have changed creatives)."""
    update: dict = {}
    if prompts:
        notes = dict(state.get("notes") or {})
        notes["budget"] = "\n".join(p.prompt for p in prompts)
        update |= budget_step({**state, "notes": notes})
    update |= targeting_step({**state, **update})
    update |= launch_step({**state, **update})
    return update


def activate(state: RuneState) -> dict:
    result = launch.set_status(state["client_id"], LaunchResult.model_validate(state["launch"]), "ACTIVE")
    bus.say(state["client_id"], "shlok.launch", "human", "INFORM", {"text": "Ads are live", "dry_run": result.dry_run})
    return {"launch": result.model_dump(mode="json"), "live": True}
