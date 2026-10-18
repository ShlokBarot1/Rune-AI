"""S5 Monitor, S6 Optimizer, S7 Creative Feedback: the loop that runs while ads are live.

cycle(state) = pull insights (Meta or simulator) -> detect problems -> propose actions ->
apply low-risk ones automatically -> ask Malav's agents for new creatives where ads fatigue.
Budget moves and kills wait for human approval (apply_action).
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

import malav.pipeline as malav
from auditor.agent import deterministic_checks
from core import bus
from core.bus import connect
from core.schemas import (BudgetPlan, ClientBrief, CreativeFeedback, CreativePackage, InsightRow, LaunchResult,
                          TargetingSpec, new_id, now)
from core.state import RuneState

from . import launch, meta, report, simulator

MAX_SCALE_STEP = 0.20        # never raise an ad set more than 20% at once (keeps Meta learning intact)
SCALE_COOLDOWN = timedelta(hours=48)
FATIGUE_FREQUENCY = 3.0


class Alert(BaseModel):
    kind: Literal["no_delivery", "high_cpa", "fatigue", "low_ctr", "winner", "pacing"]
    ref: str
    severity: Literal["info", "warn", "critical"]
    message: str


class Action(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    kind: Literal["pause_ad", "scale_adset", "cut_adset", "refresh_creatives", "launch_creatives"]
    ref: str
    reason: str
    risk: Literal["auto", "approval"]
    change: dict = {}
    status: Literal["proposed", "applied", "rejected"] = "proposed"
    ts: datetime = Field(default_factory=now)


# ---------- storage ----------

def _db():
    con = connect()
    con.execute("create table if not exists insights (client_id text, ts text, ad_ref text, adset_ref text, row text,"
                " primary key (client_id, ts, ad_ref))")
    con.execute("create table if not exists optimizer_actions (id text primary key, client_id text, ts text, action text)")
    return con


def save_insights(client_id: str, rows: list[InsightRow]):
    with _db() as con:
        con.executemany("insert or replace into insights values (?,?,?,?,?)",
                        [(client_id, r.ts.isoformat(), r.ad_ref, r.adset_ref, r.model_dump_json()) for r in rows])


def load_insights(client_id: str) -> list[InsightRow]:
    with _db() as con:
        rows = con.execute("select row from insights where client_id=? order by ts", (client_id,)).fetchall()
    return [InsightRow.model_validate_json(r["row"]) for r in rows]


def save_action(client_id: str, a: Action):
    with _db() as con:
        con.execute("insert or replace into optimizer_actions values (?,?,?,?)",
                    (a.id, client_id, a.ts.isoformat(), a.model_dump_json()))


def load_actions(client_id: str) -> list[Action]:
    with _db() as con:
        rows = con.execute("select action from optimizer_actions where client_id=? order by ts", (client_id,)).fetchall()
    return [Action.model_validate_json(r["action"]) for r in rows]


# ---------- S5 monitor ----------

def fetch_meta(client_id: str) -> list[InsightRow]:
    """Daily ad-level insights for the last 7 days from the Insights API."""
    by_meta = {o.meta_id: o.local_ref.split(":", 1)[1] for o in launch.existing(client_id).values() if o.kind == "ad"}
    data = meta.api("GET", f"{meta.account()}/insights", params={
        "level": "ad", "time_increment": 1, "date_preset": "last_7d", "limit": 500,
        "fields": "ad_id,adset_name,spend,impressions,clicks,frequency,actions,action_values,video_thruplay_watched_actions",
    }).get("data", [])
    count = lambda lst, types: sum(float(a["value"]) for a in (lst or []) if a["action_type"] in types)
    conv_types = ("purchase", "offsite_conversion.fb_pixel_purchase", "lead", "offsite_conversion.fb_pixel_lead")
    rows = []
    for d in data:
        ref = by_meta.get(d["ad_id"])
        if not ref:
            continue
        rows.append(InsightRow(
            ts=datetime.fromisoformat(d["date_start"]).replace(tzinfo=timezone.utc), ad_ref=ref,
            adset_ref=d.get("adset_name", ""), spend=float(d.get("spend", 0)), impressions=int(d.get("impressions", 0)),
            clicks=int(d.get("clicks", 0)), frequency=float(d.get("frequency", 0)),
            conversions=int(count(d.get("actions"), conv_types)), revenue=count(d.get("action_values"), conv_types),
            video_3s=int(count(d.get("actions"), ("video_view",))),
            thruplays=int(count(d.get("video_thruplay_watched_actions"), ("video_view",))), source="meta"))
    return rows


def ad_stats(rows: list[InsightRow]) -> dict[str, dict]:
    by_ad: dict[str, list[InsightRow]] = {}
    for r in rows:
        by_ad.setdefault(r.ad_ref, []).append(r)
    out = {}
    for ref, rs in by_ad.items():
        rs.sort(key=lambda r: r.ts)
        ctr = lambda xs: sum(x.clicks for x in xs) / max(1, sum(x.impressions for x in xs))
        spend, conv = sum(r.spend for r in rs), sum(r.conversions for r in rs)
        impr = sum(r.impressions for r in rs)
        out[ref] = {
            "adset_ref": rs[0].adset_ref, "days": len(rs), "spend": round(spend, 2), "impressions": impr,
            "clicks": sum(r.clicks for r in rs), "conversions": conv, "revenue": sum(r.revenue for r in rs),
            "ctr": ctr(rs), "ctr_first3": ctr(rs[:3]), "ctr_last3": ctr(rs[-3:]),
            "cpa": spend / conv if conv else None, "frequency": rs[-1].frequency,
            "hook_rate": sum(r.video_3s for r in rs) / impr if impr and any(r.video_3s for r in rs) else None,
        }
    return out


def monitor(brief: ClientBrief, stats: dict[str, dict]) -> list[Alert]:
    target = brief.kpi_targets.target_cpa
    alerts: list[Alert] = []
    for ref, s in stats.items():
        if s["impressions"] == 0 and s["days"] >= 1:
            alerts.append(Alert(kind="no_delivery", ref=ref, severity="critical", message="No impressions after a day live."))
        if target and s["spend"] >= 2 * target and s["conversions"] == 0:
            alerts.append(Alert(kind="high_cpa", ref=ref, severity="critical",
                                message=f"Spent {s['spend']:.0f} (2x target CPA) with zero conversions."))
        elif target and s["cpa"] and s["cpa"] > 1.5 * target and s["spend"] >= 3 * target:
            alerts.append(Alert(kind="high_cpa", ref=ref, severity="warn", message=f"CPA {s['cpa']:.0f} vs target {target:.0f}."))
        if s["frequency"] > FATIGUE_FREQUENCY or (s["days"] >= 5 and s["ctr_last3"] < 0.7 * s["ctr_first3"]):
            alerts.append(Alert(kind="fatigue", ref=ref, severity="warn",
                                message=f"Fatigue: frequency {s['frequency']:.1f}, CTR {s['ctr_first3']:.2%} -> {s['ctr_last3']:.2%}."))
        if target and s["cpa"] and s["cpa"] <= 0.8 * target and s["conversions"] >= 5:
            alerts.append(Alert(kind="winner", ref=ref, severity="info", message=f"Winner: CPA {s['cpa']:.0f} vs target {target:.0f}."))
    by_adset: dict[str, list[float]] = {}
    for s in stats.values():
        by_adset.setdefault(s["adset_ref"], []).append(s["ctr"])
    for ref, s in stats.items():
        median = statistics.median(by_adset[s["adset_ref"]])
        if s["impressions"] >= 1000 and len(by_adset[s["adset_ref"]]) > 1 and s["ctr"] < 0.5 * median:
            alerts.append(Alert(kind="low_ctr", ref=ref, severity="warn",
                                message=f"CTR {s['ctr']:.2%} is under half the ad-set median {median:.2%}."))
    return alerts


# ---------- S6 optimizer ----------

def optimize(client_id: str, brief: ClientBrief, plan: BudgetPlan, stats: dict[str, dict],
             alerts: list[Alert]) -> list[Action]:
    actions: list[Action] = []
    history = load_actions(client_id)
    paused = {a.ref for a in history if a.kind == "pause_ad" and a.status == "applied"}
    waiting = {(a.kind, a.ref) for a in history if a.status == "proposed"}  # don't re-propose what's already pending
    for al in alerts:
        if al.kind in ("high_cpa", "low_ctr") and al.severity in ("critical", "warn") and al.ref not in paused:
            if al.kind == "low_ctr" or al.severity == "critical":
                actions.append(Action(kind="pause_ad", ref=al.ref, reason=al.message, risk="auto"))

    # ad-set level: scale winners, cut losers
    target = brief.kpi_targets.target_cpa
    recent_scales = {a.ref: a.ts for a in load_actions(client_id) if a.kind == "scale_adset" and a.status == "applied"}
    reserve = plan.testing_reserve
    for adset_ref, daily in plan.adset_daily.items():
        ss = [s for s in stats.values() if s["adset_ref"] == adset_ref]
        spend, conv = sum(s["spend"] for s in ss), sum(s["conversions"] for s in ss)
        cpa = spend / conv if conv else None
        if not target or not ss:
            continue
        if cpa and cpa <= 0.8 * target and conv >= 10:
            if adset_ref in recent_scales and now() - recent_scales[adset_ref] < SCALE_COOLDOWN:
                continue
            step = min(daily * MAX_SCALE_STEP, reserve)
            if step >= 1:
                reserve -= step
                actions.append(Action(kind="scale_adset", ref=adset_ref, risk="approval",
                                      reason=f"Ad-set CPA {cpa:.0f} ≤ 80% of target {target:.0f} on {conv} conversions.",
                                      change={"daily_from": daily, "daily_to": round(daily + step)}))
        elif spend >= 5 * target and (cpa is None or cpa > 1.5 * target):
            actions.append(Action(kind="cut_adset", ref=adset_ref, risk="approval",
                                  reason=f"Spent {spend:.0f} with CPA {cpa or float('inf'):.0f} (>1.5x target)."))

    fatigued = {stats[a.ref]["adset_ref"] for a in alerts if a.kind == "fatigue"}
    for adset_ref in fatigued:
        actions.append(Action(kind="refresh_creatives", ref=adset_ref, risk="auto",
                              reason="Creatives fatiguing; asking Malav's agents for new variants."))
    return [a for a in actions if (a.kind, a.ref) not in waiting]


# ---------- S7 feedback ----------

def build_feedback(brief: ClientBrief, pkg: CreativePackage, adset_ref: str, stats: dict[str, dict]) -> CreativeFeedback:
    hooks = {c.creative_id: c.hook_id for c in pkg.creatives}
    ss = sorted(((ref, s) for ref, s in stats.items() if s["adset_ref"] == adset_ref), key=lambda x: -x[1]["ctr"])
    winners = [{"creative_id": r, "hook_id": hooks.get(r), "ctr": round(s["ctr"], 4), "cpa": s["cpa"],
                "hook_rate": s["hook_rate"]} for r, s in ss[:2]]
    losers = [{"creative_id": r, "hook_id": hooks.get(r), "reason": f"CTR {s['ctr']:.2%}, freq {s['frequency']:.1f}"}
              for r, s in ss[-2:] if (r, s) not in ss[:2]]
    best = winners[0] if winners else {}
    return CreativeFeedback(
        client_id=brief.client_id, adset_ref=adset_ref, winners=winners, losers=losers,
        insight=f"Best creative {best.get('creative_id')} (hook {best.get('hook_id')}) leads on CTR; others are fatiguing.",
        n_new=3, formats=sorted({f"{c.kind} {c.ratio}" for c in pkg.creatives if c.adset_ref == adset_ref}),
        direction="Iterate on the winning hook with fresh opening visuals; drop the losing angles.")


# ---------- the loop ----------

def cycle(state: RuneState, source: Literal["simulated", "meta"] = "simulated", day: int = 0, days: int = 7) -> dict:
    """One monitoring pass. Returns state updates plus the actions taken/proposed."""
    cid = state["client_id"]
    brief = ClientBrief.model_validate(state["brief"])
    pkg = CreativePackage.model_validate(state["package"])
    plan = BudgetPlan.model_validate(state["budget_plan"])
    result = LaunchResult.model_validate(state["launch"])
    if plan.target_cpa:  # the CPA agreed at the budget gate wins over the raw brief
        brief = brief.model_copy(update={"kpi_targets": brief.kpi_targets.model_copy(update={"target_cpa": plan.target_cpa})})

    rows = simulator.simulate(brief, pkg, plan, days=days, start_day=day) if source == "simulated" else fetch_meta(cid)
    save_insights(cid, rows)
    stats = ad_stats(load_insights(cid))
    alerts = monitor(brief, stats)
    for al in alerts:
        bus.say(cid, "shlok.monitor", "shlok.optimizer", "INFORM", al.model_dump())
    actions = optimize(cid, brief, plan, stats, alerts)

    update: dict = {}
    for a in actions:
        if a.risk == "auto" and a.kind == "pause_ad":
            ads = [o for o in result.objects if o.local_ref == f"ad:{a.ref}"]
            launch.set_status(cid, LaunchResult(dry_run=result.dry_run, objects=ads), "PAUSED")
            a.status = "applied"
        elif a.kind == "refresh_creatives":
            fb = build_feedback(brief, pkg, a.ref, stats)
            bus.say(cid, "shlok.feedback", "malav.hooks", "REQUEST", fb.model_dump())
            new = malav.handle_feedback(state, fb)
            a.status = "applied"
            if new.creatives:
                merged = pkg.model_copy(update={"creatives": pkg.creatives + new.creatives})
                tmp = {**state, "package": merged.model_dump(mode="json")}
                built = launch.build(brief, merged, plan, [TargetingSpec.model_validate(t) for t in state["targeting"]])
                tmp["launch"] = built.model_dump(mode="json")
                blockers = [f for f in deterministic_checks(tmp) if f.severity == "blocker"]
                if blockers:
                    bus.say(cid, "auditor", "malav", "FIX_PROMPT", {"findings": [b.model_dump() for b in blockers]})
                else:
                    update |= {"package": tmp["package"], "launch": tmp["launch"]}
                    pending = Action(kind="launch_creatives", ref=a.ref, risk="approval",
                                     reason=f"{len(new.creatives)} new creatives passed audit; approve to go live.",
                                     change={"creative_ids": [c.creative_id for c in new.creatives]})
                    actions.append(pending)
        save_action(cid, a)
        bus.say(cid, "shlok.optimizer", "human" if a.risk == "approval" else "theater", "INFORM", a.model_dump(mode="json"))
    if update.get("launch") is None:
        update["launch"] = result.model_dump(mode="json")
    acts = [a.model_dump(mode="json") for a in actions]
    return {"update": update, "alerts": [a.model_dump() for a in alerts], "actions": acts, "stats": stats,
            "report": report.daily_report(brief, plan, stats, acts)}


def apply_action(state: RuneState, action_id: str, approve: bool) -> dict:
    """Human decision on an 'approval' action. Returns state updates."""
    cid = state["client_id"]
    a = next(x for x in load_actions(cid) if x.id == action_id)
    if not approve:
        a.status = "rejected"
        save_action(cid, a)
        return {}
    plan = BudgetPlan.model_validate(state["budget_plan"])
    result = LaunchResult.model_validate(state["launch"])
    objs = {o.local_ref: o for o in result.objects}
    update: dict = {}
    if a.kind == "scale_adset":
        new_daily = a.change["daily_to"]
        delta = new_daily - plan.adset_daily[a.ref]
        if not result.dry_run:
            meta.api("POST", objs[f"adset:{a.ref}"].meta_id, data={"daily_budget": int(new_daily * 100)})
        plan.adset_daily[a.ref] = new_daily
        plan.testing_reserve = max(0, plan.testing_reserve - delta)
        update["budget_plan"] = plan.model_dump(mode="json")
    elif a.kind == "cut_adset":
        launch.set_status(cid, LaunchResult(dry_run=result.dry_run, objects=[objs[f"adset:{a.ref}"]]), "PAUSED")
        plan.testing_reserve += plan.adset_daily[a.ref]  # freed budget returns to the reserve
        plan.adset_daily[a.ref] = 0
        update["budget_plan"] = plan.model_dump(mode="json")
    elif a.kind == "launch_creatives":
        ads = [objs[f"ad:{c}"] for c in a.change["creative_ids"] if f"ad:{c}" in objs]
        launch.set_status(cid, LaunchResult(dry_run=result.dry_run, objects=ads), "ACTIVE")
    update["launch"] = result.model_dump(mode="json")
    a.status = "applied"
    save_action(cid, a)
    bus.say(cid, "human", "shlok.optimizer", "RESPONSE", {"action": a.id, "approved": True})
    return update
