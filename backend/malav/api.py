"""Malav's HTTP endpoints (included by api.py): uploads + human edits of Malav-owned state at a gate.

Edits only apply while the run is paused at a gate, and only to Malav's keys (brief, hooks, package).
The edit is recorded as if the gated step had written it, so the graph resumes from the same gate.
"""
from __future__ import annotations

import base64
import re
import sys
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import bus
from core.schemas import ClientBrief, CreativePackage
from malav.hooks import HookPlus
from malav.media import MEDIA, rel

router = APIRouter(prefix="/malav")
_graph = None
EDITABLE = {"brief": ClientBrief, "package": CreativePackage, "hooks": None}


def bind(graph) -> None:
    global _graph
    _graph = graph


class Upload(BaseModel):
    filename: str
    data: str          # base64 (a data: URL prefix is fine)


class Edit(BaseModel):
    key: str
    value: Any


@router.post("/runs/{cid}/upload")
def upload(cid: str, body: Upload):
    raw = base64.b64decode(body.data.split(",", 1)[-1])
    if len(raw) > 15_000_000:
        raise HTTPException(413, "File too large (max 15 MB)")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", body.filename)[-80:] or "upload.png"
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise HTTPException(415, "Upload a PNG, JPG or WEBP image")
    d = MEDIA / re.sub(r"[^A-Za-z0-9_-]", "", cid) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(raw)
    bus.log(cid, "malav.intake", f"Received your upload: {name}")
    return {"path": rel(d / name)}


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
    if body.key == "brief":
        value["client_id"] = cid
    stage = STAGE_OF[body.key]
    if f"{stage}__gate" in snap.next:  # at its own gate: recorded as the step's output, the gate stays pending
        _graph.update_state(cfg, {body.key: value}, as_node=stage)
        _graph.invoke(None, cfg)
    else:  # later in the run: rewind to where this key was approved, so everything after it is rebuilt
        _rewind(cfg, stage, {body.key: value})
    bus.log(cid, "malav", f"You edited the {body.key} by hand")
    return {"ok": True}
