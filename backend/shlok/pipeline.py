"""SHLOK'S PIPELINE: budget -> targeting -> Meta build (PAUSED). Activation happens after go-live.

Contract with the rest of the system:
  * STEPS: list[Step]; reads state["brief"], state["package"]; writes budget_plan, targeting, launch
  * apply_fixes(state, prompts) -> dict   (called every audit fix round, after Malav's fixes)
  * activate(state) -> dict               (after human go-live)
"""
from __future__ import annotations

from pydantic import BaseModel

from core import bus, llm
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
    current = state.get("budget_plan", {}).get("adset_daily", {})
    try:
        return llm.structured(
            "reason", "Convert the request into relative budget weights per ad set (1.0 = current equal share).",
            f"Ad sets: {adsets}\nCurrent daily budgets: {current}\nRequest: {note}", _Weights).adset_weights
    except Exception as e:
        bus.log(state["client_id"], "shlok.budget", f"could not parse budget request ({e.__class__.__name__}); keeping equal split")
        return None


def budget_step(state: RuneState) -> dict:
    brief = ClientBrief.model_validate(state["brief"])
    pkg = CreativePackage.model_validate(state["package"])
    note = (state.get("notes") or {}).get("budget")
    weights = _weights_from_note(state, note) if note else None
    plan = budget.allocate(brief, pkg.blueprint, weights)
    bus.log(brief.client_id, "shlok.budget", plan.rationale, adset_daily=plan.adset_daily)
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
