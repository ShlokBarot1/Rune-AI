"""Shlok's endpoints: live performance, optimizer actions, monitoring cycles, and the scheduler."""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Callable, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import bus
from core.schemas import BudgetPlan, ClientBrief

from . import live, meta, report

router = APIRouter(prefix="/runs/{cid}", tags=["shlok"])
_graph = None


def bind(graph):
    global _graph
    _graph = graph


def _cfg(cid: str) -> dict:
    return {"configurable": {"thread_id": cid}}


def _live_state(cid: str) -> dict:
    v = _graph.get_state(_cfg(cid)).values
    if not v.get("live"):
        raise HTTPException(409, "Campaign is not live yet")
    return v


def _save(cid: str, update: dict):
    if update:
        _graph.update_state(_cfg(cid), update, as_node="activate")


class CycleReq(BaseModel):
    source: Optional[Literal["simulated", "meta"]] = None   # default: meta when connected, else simulated
    day: int = 0
    days: int = 7


class Decision(BaseModel):
    approve: bool


def run_cycle(cid: str, source: Optional[str] = None, day: int = 0, days: int = 7) -> dict:
    state = _live_state(cid)
    # sandbox accounts never deliver: RUNE_INSIGHTS=simulated keeps real launches but simulates performance
    default = "simulated" if meta.dry_run() or os.getenv("RUNE_INSIGHTS") == "simulated" else "meta"
    out = live.cycle(state, source=source or default, day=day, days=days)
    _save(cid, out.pop("update"))
    return out


@router.post("/cycle")
def cycle(cid: str, body: CycleReq):
    return run_cycle(cid, body.source, body.day, body.days)


@router.get("/performance")
def performance(cid: str):
    v = _live_state(cid)
    rows = live.load_insights(cid)
    stats = live.ad_stats(rows)
    daily: dict[str, dict] = defaultdict(lambda: {"spend": 0.0, "conversions": 0, "revenue": 0.0, "clicks": 0, "impressions": 0})
    for r in rows:
        d = daily[r.ts.date().isoformat()]
        d["spend"] += r.spend; d["conversions"] += r.conversions; d["revenue"] += r.revenue
        d["clicks"] += r.clicks; d["impressions"] += r.impressions
    brief = ClientBrief.model_validate(v["brief"])
    plan = BudgetPlan.model_validate(v["budget_plan"])
    if plan.target_cpa:
        brief = brief.model_copy(update={"kpi_targets": brief.kpi_targets.model_copy(update={"target_cpa": plan.target_cpa})})
    return {
        "totals": report.totals(stats), "stats": stats,
        "daily": [{"date": k, **d} for k, d in sorted(daily.items())],
        "alerts": [a.model_dump() for a in live.monitor(brief, stats)] if stats else [],
        "actions": [a.model_dump(mode="json") for a in live.load_actions(cid)],
        "plan": plan.model_dump(mode="json"),
    }


@router.post("/actions/{action_id}")
def decide(cid: str, action_id: str, body: Decision):
    state = _live_state(cid)
    try:
        update = live.apply_action(state, action_id, body.approve)
    except StopIteration:
        raise HTTPException(404, "Unknown action")
    _save(cid, update)
    return {"ok": True}


def start_scheduler(run_ids: Callable[[], list[str]]):
    """S5 runs on its own: every RUNE_MONITOR_HOURS (default 4) for every live client."""
    hours = float(os.getenv("RUNE_MONITOR_HOURS", "4"))

    def loop():
        while True:
            time.sleep(hours * 3600)
            for cid in run_ids():
                try:
                    if _graph.get_state(_cfg(cid)).values.get("live"):
                        run_cycle(cid)
                except Exception as e:  # one client's failure must not stop monitoring the others
                    bus.log(cid, "shlok.monitor", f"scheduled cycle failed: {e}")

    threading.Thread(target=loop, daemon=True).start()
