"""Malav's HTTP endpoints (included by api.py): uploads + human edits of Malav-owned state at a gate.

Edits only apply while the run is paused at a gate, and only to Malav's keys (brief, hooks, package).
The edit is recorded as if the gated step had written it, so the graph resumes from the same gate.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import bus
from core.schemas import AdCopy, AdPlan, BudgetPlan, ClientBrief, CreativePackage, new_id
from malav.creatives import landing_url
from malav.hooks import GOAL_CTA, HookPlus
from malav import own
from malav.media import MEDIA, rel

router = APIRouter(prefix="/malav")
_graph = None
EDITABLE = {"brief": ClientBrief, "package": CreativePackage, "hooks": None}


def bind(graph) -> None:
    global _graph
    _graph = graph


MAX_UPLOAD = {"image": 30_000_000, "video": 1_000_000_000, "csv": 50_000_000}


@router.post("/runs/{cid}/upload")
async def upload(cid: str, filename: str, request: Request):
    """Raw file body (`fetch(url, {method: "POST", body: file})`), streamed to disk: logos, product photos and
    the client's own ad creatives (images and videos)."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[-80:].lstrip(".") or "upload.png"
    ext = Path(name).suffix.lower()
    if ext not in own.IMAGE_EXT + own.VIDEO_EXT + (".csv",):
        raise HTTPException(415, "Upload a PNG, JPG or WEBP image, an MP4, MOV, M4V or WEBM video, or a CSV customer list")
    kind = "video" if ext in own.VIDEO_EXT else "csv" if ext == ".csv" else "image"
    limit = MAX_UPLOAD[kind]
    d = MEDIA / re.sub(r"[^A-Za-z0-9_-]", "", cid) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    out, size = d / name, 0
    while out.exists():  # two files called image.png must not overwrite each other
        out = d / f"{out.stem}_1{ext}"
    try:
        with out.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File too large (max {limit // 1_000_000} MB)")
                f.write(chunk)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    if not size:
        out.unlink(missing_ok=True)
        raise HTTPException(400, "Empty file")
    bus.log(cid, "malav.intake", f"Received your upload: {out.name}")
    return {"path": rel(out), "kind": kind}


class Edit(BaseModel):
    key: str
    value: Any


class Rewind(BaseModel):
    stage: str


STAGE_OF = {"brief": "intake", "hooks": "hooks", "package": "creatives"}


def _paused(cid: str):
    """The run's state, if it is waiting for a human (not mid-run, not live)."""
    cfg = {"configurable": {"thread_id": cid}}
    snap = _graph.get_state(cfg)
    running = getattr(sys.modules.get("api"), "_running", set())  # api.py's set of runs in flight
    if cid in running or not any(t.interrupts for t in snap.tasks):
        raise HTTPException(409, "Edits apply while the run waits for you")
    if snap.values.get("live"):
        raise HTTPException(409, "The ads are live; use the optimiser instead")
    return cfg, snap


def _rewind(cfg: dict, stage: str, values: dict | None = None) -> None:
    """Fork the run back to `stage`'s approval gate (LangGraph time travel); later stages rerun after approval."""
    gate = f"{stage}__gate"
    past = next((s for s in _graph.get_state_history(cfg) if s.next == (gate,)), None)
    if past is None:
        raise HTTPException(404, f"The run never reached the {stage} approval")
    _graph.update_state(past.config, values or {}, as_node=stage)
    _graph.invoke(None, cfg)  # re-enter the gate so its approval card is pending (it interrupts at once)


@router.post("/runs/{cid}/rewind")
def rewind(cid: str, body: Rewind):
    """Go back to a stage's approval gate, e.g. to send the targeting agent a change after the audit."""
    cfg, snap = _paused(cid)
    if f"{body.stage}__gate" not in snap.next:
        _rewind(cfg, body.stage)
        bus.log(cid, "malav", f"You reopened {body.stage}; the stages after it rerun once you approve")
    return {"ok": True}


@router.post("/runs/{cid}/edit")
def edit(cid: str, body: Edit):
    if body.key not in EDITABLE:
        raise HTTPException(403, f"'{body.key}' isn't editable here (Malav's keys: {', '.join(EDITABLE)})")
    cfg, snap = _paused(cid)
    try:
        if body.key == "hooks":
            value = [HookPlus.model_validate(h).model_dump(mode="json") for h in body.value]
        else:
            value = EDITABLE[body.key].model_validate(body.value).model_dump(mode="json")
    except Exception as e:
        raise HTTPException(422, f"Invalid {body.key}: {e}")
    if body.key == "brief":  # map pins, restricted categories, catalog eligibility follow the edited brief
        from malav.intake import enrich
        value = enrich(cid, ClientBrief.model_validate({**value, "client_id": cid})).model_dump(mode="json")
    stage = STAGE_OF[body.key]
    if f"{stage}__gate" in snap.next:  # at its own gate: recorded as the step's output, the gate stays pending
        _graph.update_state(cfg, {body.key: value}, as_node=stage)
        _graph.invoke(None, cfg)
    else:  # later in the run: rewind to where this key was approved, so everything after it is rebuilt
        _rewind(cfg, stage, {body.key: value})
    bus.log(cid, "malav", f"You edited the {body.key} by hand")
    return {"ok": True}


class NewCreatives(BaseModel):
    adset_ref: str
    creatives: list[dict]   # [{path (from /upload), primary_text?, headline?, description?, cta?}]


@router.post("/runs/{cid}/creatives")
def add_creatives(cid: str, body: NewCreatives):
    """While live: the client's fresh files become new ads in an ad set, built PAUSED and audited; they go live
    when the human approves the launch_creatives action on Live Performance."""
    from shlok import live
    cfg = {"configurable": {"thread_id": cid}}
    v = _graph.get_state(cfg).values
    if not v.get("live"):
        raise HTTPException(409, "Add creatives at the creatives step; this is for campaigns that are live")
    if cid in getattr(sys.modules.get("api"), "_running", set()):
        raise HTTPException(409, "The agents are busy with this campaign; try again in a moment")
    items = own.uploads({"client_id": cid, "intake": {"creatives": body.creatives}})
    if not items:
        raise HTTPException(422, "Upload the files first (POST /malav/runs/{cid}/upload)")
    pkg = CreativePackage.model_validate(v["package"])
    bp = pkg.blueprint.model_copy(deep=True)
    adset = next((s for c in bp.campaigns for s in c.adsets if s.ref == body.adset_ref), None)
    if not adset:
        raise HTTPException(404, f"No ad set {body.adset_ref}")
    camp = next(c for c in bp.campaigns if adset in c.adsets)
    brief = ClientBrief.model_validate(v["brief"])
    base = next((c for c in pkg.creatives if c.adset_ref == adset.ref), None)
    taken = {a.ref for a in adset.ads} | {c.ad_ref for c in pkg.creatives}
    new = []
    for a in own.probe_all(cid, items):
        n = 1
        while f"{adset.ref.replace('as_', 'ad_', 1)}_u{n}" in taken:
            n += 1
        ad_ref = f"{adset.ref.replace('as_', 'ad_', 1)}_u{n}"
        taken.add(ad_ref)
        hook_id = base.hook_id if base else f"h_{ad_ref}"
        adset.ads.append(AdPlan(ref=ad_ref, hook_id=hook_id, format=a.kind, ratios=[a.ratio]))
        copy = base.ad_copy if base else AdCopy(primary_text=brief.products[0].name, headline=brief.business_name[:40],
                                                cta=GOAL_CTA[brief.goal])
        new.append(own.to_creative(a, creative_id=new_id("cr"), campaign_ref=camp.ref, adset_ref=adset.ref,
                                   ad_ref=ad_ref, hook_id=hook_id, copy=copy,
                                   landing_url=landing_url(brief, camp.ref, ad_ref)))
    bad = [c for c in new if not c.qa.passed]
    if bad:
        raise HTTPException(422, f"{bad[0].path}: {bad[0].qa.notes}")
    update: dict = {}
    action = live.stage_creatives(v, brief, BudgetPlan.model_validate(v["budget_plan"]), pkg,
                                  CreativePackage(client_id=cid, blueprint=bp, creatives=new), adset.ref, update)
    if not action:
        raise HTTPException(422, "The audit blocked these creatives; see Agent Theater")
    _graph.update_state(cfg, update, as_node="activate")
    bus.log(cid, "malav.packager", f"{len(new)} new creative(s) from you for {adset.name}, built paused; approve to launch")
    return {"action": action.model_dump(mode="json")}
