"""Integration contract: fixtures match schemas, budget math holds, and the full graph runs end to end.

If this fails on your PR, you broke the Malav <-> Shlok contract. Fix before merging.
"""
import json
import os
from pathlib import Path

os.environ["RUNE_AUTO_APPROVE"] = "1"

from langgraph.checkpoint.memory import InMemorySaver

from core.schemas import (AuditReport, BudgetPlan, CampaignBlueprint, ClientBrief, CreativeFeedback,
                          CreativePackage, LaunchResult, ResearchReport)
from shlok import budget

FIX = Path(__file__).resolve().parent.parent / "fixtures"
load = lambda n: json.loads((FIX / f"{n}.json").read_text(encoding="utf-8"))


def test_fixtures_match_contracts():
    ClientBrief.model_validate(load("brief"))
    ResearchReport.model_validate(load("research"))
    CampaignBlueprint.model_validate(load("blueprint"))
    CreativeFeedback.model_validate(load("creative_feedback"))
    pkg = CreativePackage.model_validate(load("package"))
    planned = {a.ref for a in pkg.blueprint.adsets() for a in a.ads}
    assert planned == {c.ad_ref for c in pkg.creatives}


def test_budget_invariants():
    brief = ClientBrief.model_validate(load("brief"))
    bp = CampaignBlueprint.model_validate(load("blueprint"))
    f = budget.budget_feasibility(brief, n_adsets=2)
    assert f.feasible and f.max_adsets == 2  # 5000/day, 15% reserve, 50*200/7 floor
    assert not budget.budget_feasibility(brief, n_adsets=3).feasible

    plan = budget.allocate(brief, bp)
    assert (sum(plan.adset_daily.values()) + plan.testing_reserve) * plan.days <= brief.budget.total
    assert set(plan.adset_daily) == {s.ref for s in bp.adsets()}

    skewed = budget.allocate(brief, bp, {"as_wfh_price": 3, "as_fit_nosugar": 1})
    assert abs(skewed.adset_daily["as_wfh_price"] - 3 * skewed.adset_daily["as_fit_nosugar"]) <= 3  # floor rounding
    assert sum(skewed.adset_daily.values()) <= sum(plan.adset_daily.values()) + 1


def test_full_pipeline_runs_to_live():
    from graph import build

    app = build(InMemorySaver())
    final = app.invoke({"client_id": "demo_brewbloom"}, {"configurable": {"thread_id": "t1"}})
    report = AuditReport.model_validate(final["audit"])
    assert report.decision != "FAIL", report.findings
    BudgetPlan.model_validate(final["budget_plan"])
    assert final["live"] is True
    result = LaunchResult.model_validate(final["launch"])
    assert all(o.status == "ACTIVE" for o in result.objects if o.kind in ("campaign", "adset", "ad"))

    # live loop: simulated week -> monitor -> optimizer -> feedback to Malav; approvals apply cleanly
    from shlok import live
    out = live.cycle(final, source="simulated", days=7)
    assert out["stats"] and out["actions"]
    kinds = {a["kind"] for a in out["actions"]}
    assert kinds & {"pause_ad", "scale_adset", "cut_adset", "refresh_creatives"}
    state = {**final, **out["update"]}
    for a in out["actions"]:
        if a["risk"] == "approval":
            state |= live.apply_action(state, a["id"], approve=True)
    plan = BudgetPlan.model_validate(state["budget_plan"])
    assert (sum(plan.adset_daily.values()) + plan.testing_reserve) * plan.days <= 150000 + 1
