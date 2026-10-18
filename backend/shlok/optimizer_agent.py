"""S6 Optimizer Agent: an LLM that investigates live performance with tools and decides what to do.

Loop (max MAX_STEPS): the model returns {thought, tool, args}; code runs the tool and feeds back the
observation; repeat until it calls finish. Every step is posted to the agent bus, so Agent Theater
shows the reasoning live.

Guardrails live in the tools, not the prompt: a blocked action returns "REJECTED: <why>" as the
observation and the agent has to adapt. Pauses run immediately (low risk); budget changes, cuts and
new-creative launches become approval requests for the human.

run() returns None when no model answers at all, so live.cycle falls back to the rule-based optimizer.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from core import bus, llm
from core.schemas import BudgetPlan, ClientBrief, InsightRow, LaunchResult, now
from core.state import RuneState

from . import live
from .budget import account_min_daily

AGENT = "shlok.optimizer"
MAX_STEPS = 8

Tool = Literal["get_ads", "get_trend", "pause_ad", "propose_budget_change", "propose_cut_adset",
               "request_new_creatives", "finish"]


class AgentStep(BaseModel):
    thought: str
    tool: Tool
    args: dict = {}


SYSTEM = """You are the optimizer agent of a Meta ads agency. Each cycle you investigate the live
campaign and take the few actions that most improve results against the client's target CPA.

Tools (reply with exactly one per step):
- get_ads {"adset_ref"}: per-ad stats in an ad set (spend, CTR, conversions, CPA, frequency, hook rate)
- get_trend {"ad_ref"}: that ad's daily CTR/CPA, to tell fatigue from noise
- pause_ad {"ad_ref", "reason"}: stop a clearly losing ad (runs immediately)
- propose_budget_change {"adset_ref", "new_daily", "reason"}: raise a winner (max +20%) or lower a loser; the human approves
- propose_cut_adset {"adset_ref", "reason"}: stop a whole ad set that keeps failing; the human approves
- request_new_creatives {"adset_ref", "direction"}: ask the creative agents for fresh variants (fatigue or weak hooks)
- finish {"summary"}: end the cycle with a 1-2 sentence summary of what you did and why

Rules: you have at most {MAX_STEPS} steps, so call finish before running out; investigate before acting; base every action on numbers you observed; don't act on ads with too
little data; at most one budget change per ad set. If a tool answer starts with REJECTED, adapt.
Keep each thought to one or two sentences."""


class _Ctx:
    def __init__(self, state, brief, plan, result, stats, alerts, rows, update):
        self.state, self.brief, self.plan, self.result = state, brief, plan, result
        self.stats, self.alerts, self.rows, self.update = stats, alerts, rows, update
        self.actions: list[live.Action] = []
        self.paused = {a.ref for a in live.load_actions(state["client_id"]) if a.kind == "pause_ad" and a.status == "applied"}
        self.touched: set[tuple[str, str]] = set()
        self.target = brief.kpi_targets.target_cpa


def _overview(c: _Ctx) -> dict:
    by_set: dict[str, dict] = defaultdict(lambda: {"spend": 0.0, "conversions": 0, "ads": 0, "paused_ads": 0})
    for ref, s in c.stats.items():
        o = by_set[s["adset_ref"]]
        o["spend"] += s["spend"]; o["conversions"] += s["conversions"]; o["ads"] += 1
        o["paused_ads"] += ref in c.paused
    for ref, o in by_set.items():
        o["cpa"] = round(o["spend"] / o["conversions"]) if o["conversions"] else None
        o["daily_budget"] = c.plan.adset_daily.get(ref)
        o["spend"] = round(o["spend"])
    return {"target_cpa": c.target, "currency": c.plan.currency, "reserve_daily": c.plan.testing_reserve,
            "adsets": by_set, "alerts": [a.message for a in c.alerts][:12]}


def _tool(c: _Ctx, step: AgentStep) -> str:
    a, cid = step.args, c.state["client_id"]
    if step.tool == "get_ads":
        ads = {r: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()
                  if k in ("spend", "impressions", "conversions", "ctr", "ctr_first3", "ctr_last3", "cpa", "frequency", "hook_rate")}
               | {"paused": r in c.paused}
               for r, s in c.stats.items() if s["adset_ref"] == a.get("adset_ref")}
        return json.dumps(ads) if ads else "REJECTED: unknown adset_ref"
    if step.tool == "get_trend":
        days = [r for r in c.rows if r.ad_ref == a.get("ad_ref")]
        if not days:
            return "REJECTED: unknown ad_ref"
        return json.dumps([{"day": r.ts.date().isoformat(), "ctr": round(r.clicks / max(1, r.impressions), 4),
                            "conv": r.conversions, "spend": round(r.spend)} for r in sorted(days, key=lambda r: r.ts)])

    if step.tool == "pause_ad":
        ref, s = a.get("ad_ref"), c.stats.get(a.get("ad_ref", ""))
        if not s:
            return "REJECTED: unknown ad_ref"
        if ref in c.paused:
            return "REJECTED: already paused"
        if s["impressions"] < 1000 and (not c.target or s["spend"] < c.target):
            return "REJECTED: too little data to judge this ad yet"
        live_in_set = [r for r, x in c.stats.items() if x["adset_ref"] == s["adset_ref"] and r not in c.paused]
        if len(live_in_set) <= 1:
            return "REJECTED: it is the last active ad in its ad set; cut the ad set instead"
        in_set = [r for r, x in c.stats.items() if x["adset_ref"] == s["adset_ref"]]
        paused_now = sum(1 for x in c.actions if x.kind == "pause_ad" and x.ref in in_set)
        if paused_now + 1 > len(in_set) // 2:
            return "REJECTED: at most half of an ad set's ads can be paused per cycle; Meta needs variety to learn"
        act = live.Action(kind="pause_ad", ref=ref, risk="auto", reason=a.get("reason", ""))
        c.actions += live.execute(c.state, c.brief, c.plan, c.result, c.stats, act, c.update)
        c.paused.add(ref)
        return f"done: {ref} paused"

    if step.tool == "propose_budget_change":
        ref = a.get("adset_ref")
        if ref not in c.plan.adset_daily:
            return "REJECTED: unknown adset_ref"
        if ("budget", ref) in c.touched:
            return "REJECTED: already changed this ad set's budget this cycle"
        cur, new = c.plan.adset_daily[ref], float(a.get("new_daily", 0))
        recent = [x for x in live.load_actions(cid) if x.kind == "scale_adset" and x.ref == ref and x.status == "applied"]
        if new > cur and recent and now() - recent[-1].ts < live.SCALE_COOLDOWN:
            return "REJECTED: this ad set was scaled less than 48h ago; let Meta's learning settle"
        if new > cur * (1 + live.MAX_SCALE_STEP) + 0.5:
            return f"REJECTED: max increase is 20% (to {cur * (1 + live.MAX_SCALE_STEP):.0f})"
        if new - cur > c.plan.testing_reserve:
            return f"REJECTED: only {c.plan.testing_reserve:.0f}/day left in the reserve"
        if new < account_min_daily(c.plan.currency):
            return "REJECTED: below Meta's minimum daily budget; propose cutting the ad set instead"
        act = live.Action(kind="scale_adset", ref=ref, risk="approval", reason=a.get("reason", ""),
                          change={"daily_from": cur, "daily_to": round(new)})
        c.actions += live.execute(c.state, c.brief, c.plan, c.result, c.stats, act, c.update)
        c.touched.add(("budget", ref))
        return f"proposed to the human: {ref} {cur:.0f} -> {new:.0f}/day"

    if step.tool == "propose_cut_adset":
        ref = a.get("adset_ref")
        spend = sum(s["spend"] for s in c.stats.values() if s["adset_ref"] == ref)
        if ref not in c.plan.adset_daily:
            return "REJECTED: unknown adset_ref"
        if ("cut", ref) in c.touched:
            return "REJECTED: already proposed"
        if c.target and spend < 3 * c.target:
            return f"REJECTED: only {spend:.0f} spent; need at least 3x target CPA of data before cutting"
        act = live.Action(kind="cut_adset", ref=ref, risk="approval", reason=a.get("reason", ""))
        c.actions += live.execute(c.state, c.brief, c.plan, c.result, c.stats, act, c.update)
        c.touched.add(("cut", ref))
        return f"proposed to the human: cut {ref}"

    if step.tool == "request_new_creatives":
        ref = a.get("adset_ref")
        if ref not in c.plan.adset_daily:
            return "REJECTED: unknown adset_ref"
        if ("creatives", ref) in c.touched:
            return "REJECTED: already requested for this ad set this cycle"
        act = live.Action(kind="refresh_creatives", ref=ref, risk="auto", reason=a.get("direction", ""))
        new = live.execute(c.state, c.brief, c.plan, c.result, c.stats, act, c.update, direction=a.get("direction", ""))
        c.actions += new
        c.touched.add(("creatives", ref))
        return "sent to malav.hooks" + (f"; {len(new) - 1} new creative batch awaiting approval" if len(new) > 1 else "")
    return "REJECTED: unknown tool"


def run(state: RuneState, brief: ClientBrief, plan: BudgetPlan, result: LaunchResult, stats: dict,
        alerts: list, rows: list[InsightRow], update: dict):
    if not stats:
        return []
    c = _Ctx(state, brief, plan, result, stats, alerts, rows, update)
    cid = state["client_id"]
    transcript = [f"Cycle overview: {json.dumps(_overview(c), default=str)}"]
    system = SYSTEM.replace("{MAX_STEPS}", str(MAX_STEPS))  # no skill text: it would be resent on every step
    for i in range(MAX_STEPS):
        try:
            step = llm.structured("reason", system, "\n".join(transcript)[-8000:] + "\n\nNext step?",
                                  AgentStep, agent=AGENT)
        except Exception as e:
            if i == 0:
                bus.log(cid, AGENT, f"no model available ({e.__class__.__name__}); rule-based optimizer takes over")
                return None
            bus.log(cid, AGENT, f"stopped early: model unavailable ({e.__class__.__name__})")
            break
        if step.tool == "finish":
            bus.log(cid, AGENT, step.args.get("summary") or step.thought, step=i + 1)
            break
        observation = _tool(c, step)
        bus.log(cid, AGENT, step.thought, step=i + 1, tool=step.tool, args=step.args, observation=observation[:400])
        transcript.append(f"Step {i + 1}: {step.model_dump_json()}\nObservation: {observation[:1500]}")
    else:
        bus.log(cid, AGENT, f"step limit ({MAX_STEPS}) reached; ending cycle")
    return c.actions
