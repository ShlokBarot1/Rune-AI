"""S2 Budget Allocation Agent. Money math is deterministic code; the LLM never does arithmetic.

budget_feasibility() is the cross-team tool Malav's Strategy Agent calls before fixing the
number of ad sets.
"""
from __future__ import annotations

import math
import os
from typing import Optional

from core import bus
from core.schemas import BudgetPlan, CampaignBlueprint, ClientBrief, FeasibilityResult

from . import meta

TESTING_RESERVE = 0.15            # share of daily budget held for feedback-loop creatives
LEARNING_EVENTS_PER_WEEK = 50     # Meta learning phase needs ~50 optimisation events / ad set / week
FUNNEL_WEIGHTS = {"TOF": 0.7, "MOF": 0.2, "BOF": 0.1}


def account_min_daily(currency: str) -> float:
    """Meta's minimum daily budget per ad set (major units): from the ad account when connected,
    else META_MIN_DAILY / a conservative default."""
    if not meta.dry_run():
        try:
            return float(meta.account_facts()["min_daily_budget"]) / 100
        except (meta.MetaError, KeyError, ValueError):
            pass
    return float(os.getenv("META_MIN_DAILY", 100 if currency == "INR" else 1))


def flight_days(brief: ClientBrief) -> int:
    return (brief.budget.end - brief.budget.start).days + 1


def total_daily(brief: ClientBrief) -> float:
    daily = brief.budget.total / flight_days(brief)
    return min(daily, brief.budget.daily_cap) if brief.budget.daily_cap else daily


def min_daily_per_adset(brief: ClientBrief) -> float:
    learning = LEARNING_EVENTS_PER_WEEK * brief.kpi_targets.target_cpa / 7 if brief.kpi_targets.target_cpa else 0
    return max(account_min_daily(brief.budget.currency), learning)


def budget_feasibility(brief: ClientBrief, n_adsets: int, asked_by: str = "malav.strategy") -> FeasibilityResult:
    bus.say(brief.client_id, asked_by, "shlok.budget", "REQUEST", {"tool": "budget_feasibility", "n_adsets": n_adsets})
    daily = total_daily(brief)
    floor = min_daily_per_adset(brief)
    max_adsets = int(daily * (1 - TESTING_RESERVE) // floor)
    if n_adsets <= max_adsets:
        suggestion = f"OK: {n_adsets} ad sets at ≥{floor:,.0f} {brief.budget.currency}/day each fit in {daily:,.0f}/day."
    elif max_adsets >= 1:
        suggestion = (f"Budget supports {max_adsets} ad set(s), not {n_adsets}: each needs ≥{floor:,.0f}/day to exit "
                      f"Meta's learning phase at the target CPA. Merge personas or test angles as ads inside fewer ad sets.")
    else:
        suggestion = ("Budget is below one ad set's learning threshold at this CPA. Use 1 ad set and optimise for a "
                      "cheaper upstream event (landing page views / add to cart) until conversions accumulate.")
    result = FeasibilityResult(feasible=n_adsets <= max_adsets, n_adsets_requested=n_adsets, max_adsets=max_adsets,
                               min_daily_per_adset=round(floor, 2), total_daily=round(daily, 2), suggestion=suggestion)
    bus.say(brief.client_id, "shlok.budget", asked_by, "RESPONSE", result.model_dump())
    return result


def allocate(brief: ClientBrief, blueprint: CampaignBlueprint,
             adset_weights: Optional[dict[str, float]] = None) -> BudgetPlan:
    """ABO testing structure: funnel weights across campaigns, then split each campaign across its
    ad sets by `adset_weights` (default equal). Human budget requests become weights (see pipeline)."""
    daily = total_daily(brief)
    reserve = math.floor(daily * TESTING_RESERVE)
    spendable = daily - reserve
    stages = {c.funnel_stage for c in blueprint.campaigns}
    weight_sum = sum(FUNNEL_WEIGHTS[s] for s in stages)
    per_stage_count = {s: sum(1 for c in blueprint.campaigns if c.funnel_stage == s) for s in stages}

    campaign_daily: dict[str, float] = {}
    adset_daily: dict[str, float] = {}
    for c in blueprint.campaigns:
        c_budget = spendable * FUNNEL_WEIGHTS[c.funnel_stage] / weight_sum / per_stage_count[c.funnel_stage]
        w = {s.ref: max(0.0, (adset_weights or {}).get(s.ref, 1.0)) for s in c.adsets}
        w_sum = sum(w.values()) or 1.0
        for s in c.adsets:  # whole currency units; remainder stays unspent
            adset_daily[s.ref] = math.floor(c_budget * w[s.ref] / w_sum)
        campaign_daily[c.ref] = sum(adset_daily[s.ref] for s in c.adsets)

    floor = account_min_daily(brief.budget.currency)
    too_small = [ref for ref, v in adset_daily.items() if v < floor]
    if too_small:
        raise ValueError(f"Ad sets below Meta minimum {floor}/day: {too_small}. Reduce ad sets (see budget_feasibility).")

    days = flight_days(brief)
    assert (sum(adset_daily.values()) + reserve) * days <= brief.budget.total + 1e-6
    learning_floor = min_daily_per_adset(brief)
    rationale = (
        f"{brief.budget.total:,.0f} {brief.budget.currency} over {days} days = {daily:,.0f}/day. "
        f"{reserve:,.0f}/day ({TESTING_RESERVE:.0%}) reserved for new creatives from live learnings. "
        f"Ad-set budgets (ABO) so every angle gets a fair test; switch winners to campaign budget when scaling. "
        f"Funnel split {', '.join(f'{s} {FUNNEL_WEIGHTS[s] / weight_sum:.0%}' for s in sorted(stages))}. "
        f"Lowest-cost bidding until conversion data exists. Learning-phase floor ≈ {learning_floor:,.0f}/day per ad set"
        + (" — some ad sets are below it, expect slower learning." if min(adset_daily.values()) < learning_floor else ".")
    )
    if adset_weights:
        rationale += f" Ad-set weights applied: {adset_weights}."
    return BudgetPlan(client_id=brief.client_id, currency=brief.budget.currency, total=brief.budget.total, days=days,
                      mode="ABO", bid_strategy="LOWEST_COST_WITHOUT_CAP", testing_reserve=reserve,
                      campaign_daily=campaign_daily, adset_daily=adset_daily, rationale=rationale)
