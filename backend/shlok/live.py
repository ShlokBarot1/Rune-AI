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

from . import launch, meta, optimizer_agent, report, simulator

MAX_SCALE_STEP = 0.20        # never raise an ad set more than 20% at once (keeps Meta learning intact)
SCALE_COOLDOWN = timedelta(hours=48)
FATIGUE_FREQUENCY = 3.0


class Alert(BaseModel):
    kind: Literal["no_delivery", "high_cpa", "fatigue", "low_ctr", "winner", "pacing", "overspend", "cpa_spike",
                  "budget_cap", "offer_ended", "disapproved"]
    ref: str
    severity: Literal["info", "warn", "critical"]
    message: str


class Action(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    kind: Literal["pause_ad", "scale_adset", "cut_adset", "refresh_creatives", "launch_creatives", "pause_campaign",
                  "fix_disapproved"]
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


def anomalies(plan: BudgetPlan, rows: list[InsightRow]) -> list[Alert]:
    """Money alarms over the whole account, day by day: a day far over plan, the budget nearly gone, a CPA that
    suddenly doubled. (Meta's campaign spend_cap set at launch is the hard backstop; these catch it earlier.)"""
    days: dict = {}
    for r in rows:
        d = days.setdefault(r.ts.date(), [0.0, 0])
        d[0] += r.spend
        d[1] += r.conversions
    if not days:
        return []
    out, order = [], sorted(days)
    last, planned = order[-1], sum(plan.adset_daily.values()) + plan.testing_reserve
    if planned and days[last][0] > 1.5 * planned:
        out.append(Alert(kind="overspend", ref="account", severity="critical",
                         message=f"{last}: spent {days[last][0]:,.0f}, {days[last][0] / planned:.1f}x the {planned:,.0f}/day plan."))
    budget, spent = plan.net_total or plan.total, sum(v[0] for v in days.values())
    if spent >= 0.95 * budget:
        out.append(Alert(kind="budget_cap", ref="account", severity="critical",
                         message=f"Spent {spent:,.0f} of the {budget:,.0f} budget: pausing everything before it overruns."))
    before = [days[d] for d in order[:-1] if days[d][1]]
    if len(before) >= 3 and days[last][1] >= 1:
        avg = sum(v[0] for v in before) / sum(v[1] for v in before)
        today = days[last][0] / days[last][1]
        if today > 2 * avg:
            out.append(Alert(kind="cpa_spike", ref="account", severity="warn",
                             message=f"{last}: cost per result {today:,.0f}, over 2x the {avg:,.0f} average so far."))
    return out


def offer_ended(brief: ClientBrief, pkg: CreativePackage, today) -> list[str]:
    """Creatives that still advertise an offer that is over (its badge, its code or its headline)."""
    o = brief.offer
    if not o or not o.ends or today <= o.ends:
        return []
    marks = [m.lower() for m in (o.code, o.headline) if m]
    return [c.creative_id for c in pkg.creatives
            if c.template_id == "offer-badge" or any(m in f"{c.ad_copy.primary_text} {c.ad_copy.headline}".lower()
                                                    for m in marks)]


def fetch_review(result: LaunchResult) -> list[tuple[str, str]]:
    """(creative id, Meta's reason) for every ad Meta disapproved. Live accounts only."""
    out = []
    for o in result.objects:
        if o.kind != "ad" or o.meta_id.startswith("dry_"):
            continue
        try:
            info = meta.api("GET", o.meta_id, params={"fields": "effective_status,ad_review_feedback"})
        except meta.MetaError:
            continue
        if info.get("effective_status") == "DISAPPROVED":
            fb = info.get("ad_review_feedback") or {}
            reason = "; ".join(f"{k}: {v}" for part in fb.values() if isinstance(part, dict) for k, v in part.items())
            out.append((o.local_ref.split(":", 1)[1].split("@")[0], reason or "Meta disapproved the ad"))
    return out


def safety(state: RuneState, brief: ClientBrief, plan: BudgetPlan, pkg: CreativePackage, result: LaunchResult,
           rows: list[InsightRow], source: str, update: dict) -> tuple[list[Alert], list[Action]]:
    """Alarms that act on their own, before the optimiser: the budget cap, ended offers, disapproved ads."""
    cid, alerts, actions = state["client_id"], anomalies(plan, rows), []
    if any(a.kind == "budget_cap" for a in alerts):
        launch.set_status(cid, result, "PAUSED", kinds=("campaign",))
        actions.append(Action(kind="pause_campaign", ref="all", risk="auto", status="applied",
                              reason="Budget nearly spent: every campaign paused so nothing overruns."))
    today = max((r.ts.date() for r in rows), default=None) if source == "simulated" else now().date()
    paused = {a.ref for a in load_actions(cid) if a.kind == "pause_ad" and a.status == "applied"}
    for cr in offer_ended(brief, pkg, today) if today else []:
        if cr in paused:
            continue
        ads = [o for o in result.objects if o.kind == "ad" and o.local_ref.split(":", 1)[1].split("@")[0] == cr]
        launch.set_status(cid, LaunchResult(dry_run=result.dry_run, objects=ads), "PAUSED")
        alerts.append(Alert(kind="offer_ended", ref=cr, severity="info", message=f"The offer ended on {brief.offer.ends}."))
        actions.append(Action(kind="pause_ad", ref=cr, risk="auto", status="applied",
                              reason=f"Its offer ended on {brief.offer.ends}; it would advertise a deal that's over."))
    if source == "meta":
        waiting = {a.ref for a in load_actions(cid) if a.kind == "fix_disapproved" and a.status == "proposed"}
        for cr, reason in fetch_review(result):
            if cr in waiting:
                continue
            alerts.append(Alert(kind="disapproved", ref=cr, severity="critical", message=f"Meta disapproved it: {reason}"))
            actions.append(Action(kind="fix_disapproved", ref=cr, risk="approval", change={"reason": reason},
                                  reason=f"Meta disapproved it ({reason[:160]}). Approve to relaunch it with rewritten copy."))
    for a in actions:
        save_action(cid, a)
        bus.say(cid, "shlok.monitor", "human" if a.risk == "approval" else "theater", "INFORM", a.model_dump(mode="json"))
    if actions:
        update["launch"] = result.model_dump(mode="json")
    return alerts, actions


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


# ---------- executing actions (shared by the optimizer agent and the rule fallback) ----------

def execute(state: RuneState, brief: ClientBrief, plan: BudgetPlan, result: LaunchResult, stats: dict[str, dict],
            a: Action, update: dict, direction: str = "") -> list[Action]:
    """Apply an 'auto' action now (pause, ask for creatives); 'approval' actions are only recorded.
    `update` collects state changes (new package / launch). Returns the actions to report."""
    cid = state["client_id"]
    out = [a]
    if a.risk == "auto" and a.kind == "pause_ad":
        ads = [o for o in result.objects if o.local_ref == f"ad:{a.ref}"]
        launch.set_status(cid, LaunchResult(dry_run=result.dry_run, objects=ads), "PAUSED")
        a.status = "applied"
    elif a.kind == "refresh_creatives":
        pkg = CreativePackage.model_validate(update.get("package") or state["package"])
        fb = build_feedback(brief, pkg, a.ref, stats)
        if direction:
            fb.direction = direction
        bus.say(cid, "shlok.feedback", "malav.hooks", "REQUEST", fb.model_dump())
        new = malav.handle_feedback(state, fb)
        a.status = "applied"
        if pending := stage_creatives(state, brief, plan, pkg, new, a.ref, update):
            out.append(pending)
    save_action(cid, a)
    bus.say(cid, "shlok.optimizer", "human" if a.risk == "approval" else "theater", "INFORM", a.model_dump(mode="json"))
    return out


def stage_creatives(state: RuneState, brief: ClientBrief, plan: BudgetPlan, pkg: CreativePackage,
                    new: CreativePackage, adset_ref: str, update: dict) -> Action | None:
    """New creatives (feedback loop or client uploads) -> built PAUSED in Meta, audited, and a launch_creatives
    approval for the human. Returns that action, or None when nothing new or the audit blocks it."""
    cid = state["client_id"]
    if not new.creatives:
        return None
    merged = pkg.model_copy(update={"blueprint": new.blueprint, "creatives": pkg.creatives + new.creatives})
    tmp = {**state, "package": merged.model_dump(mode="json")}
    built = launch.build(brief, merged, plan, [TargetingSpec.model_validate(t) for t in state["targeting"]])
    tmp["launch"] = built.model_dump(mode="json")
    blockers = [f for f in deterministic_checks(tmp) if f.severity == "blocker"]
    if blockers:
        bus.say(cid, "auditor", "malav", "FIX_PROMPT", {"findings": [b.model_dump() for b in blockers]})
        return None
    update |= {"package": tmp["package"], "launch": tmp["launch"]}
    pending = Action(kind="launch_creatives", ref=adset_ref, risk="approval",
                     reason=f"{len(new.creatives)} new creatives passed audit; approve to go live.",
                     change={"creative_ids": [c.creative_id for c in new.creatives]})
    save_action(cid, pending)
    bus.say(cid, "shlok.optimizer", "human", "INFORM", pending.model_dump(mode="json"))
    return pending


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

    active = {o.local_ref.split(":", 1)[1] for o in result.objects if o.kind == "ad" and o.status == "ACTIVE"}
    rows = (simulator.simulate(brief, pkg, plan, days=days, start_day=day, active=active) if source == "simulated"
            else fetch_meta(cid))
    save_insights(cid, rows)
    stats = ad_stats(load_insights(cid))
    update: dict = {}
    safe_alerts, safe_actions = safety(state, brief, plan, pkg, result, load_insights(cid), source, update)
    alerts = safe_alerts + monitor(brief, stats)
    for al in alerts:
        bus.say(cid, "shlok.monitor", "shlok.optimizer", "INFORM", al.model_dump())
    if any(a.kind == "pause_campaign" for a in safe_actions):  # everything is paused: nothing left to optimise
        actions = []
    else:
        actions = optimizer_agent.run(state, brief, plan, result, stats, alerts, load_insights(cid), update)
        if actions is None:  # no model reachable: the rule-based optimizer acts instead
            actions = []
            for a in optimize(cid, brief, plan, stats, alerts):
                actions += execute(state, brief, plan, result, stats, a, update)
    actions = safe_actions + actions
    if update.get("launch") is None:
        update["launch"] = result.model_dump(mode="json")
    acts = [a.model_dump(mode="json") for a in actions]
    return {"update": update, "alerts": [a.model_dump() for a in alerts], "actions": acts, "stats": stats,
            "report": report.daily_report(brief, plan, stats, acts, names={
                **{c.creative_id: c.ad_copy.headline for c in pkg.creatives}, **{s.ref: s.name for s in pkg.blueprint.adsets()}})}


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
    elif a.kind == "fix_disapproved":
        from malav.hooks import rewrite_copy
        brief = ClientBrief.model_validate(state["brief"])
        pkg = CreativePackage.model_validate(state["package"])
        old = next(c for c in pkg.creatives if c.creative_id == a.ref)
        copy = rewrite_copy(cid, brief, old.ad_copy, [f"Meta disapproved this ad: {a.change.get('reason', '')}"])
        fresh = old.model_copy(update={"creative_id": new_id("cr"), "ad_ref": f"{old.ad_ref}_fix", "ad_copy": copy,
                                       "version": old.version + 1, "parent_id": old.creative_id})
        bp = pkg.blueprint.model_copy(deep=True)
        for s_ in bp.adsets():
            if s_.ref == old.adset_ref:
                s_.ads.append(next(x for x in s_.ads if x.ref == old.ad_ref).model_copy(update={"ref": fresh.ad_ref}))
        staged = stage_creatives(state, brief, plan, pkg, CreativePackage(client_id=cid, blueprint=bp, creatives=[fresh]),
                                 old.adset_ref, update)
        if not staged:
            raise ValueError("The rewritten ad didn't pass the audit; edit it in Creative Studio")
        result = LaunchResult.model_validate(update["launch"])
    elif a.kind == "launch_creatives":
        ads = [objs[f"ad:{c}"] for c in a.change["creative_ids"] if f"ad:{c}" in objs]
        launch.set_status(cid, LaunchResult(dry_run=result.dry_run, objects=ads), "ACTIVE")
    update["launch"] = result.model_dump(mode="json")
    a.status = "applied"
    save_action(cid, a)
    bus.say(cid, "human", "shlok.optimizer", "RESPONSE", {"action": a.id, "approved": True})
    return update
