"""Malav's HTTP endpoints (included by api.py): uploads + human edits of Malav-owned state at a gate.

Edits only apply while the run is paused at a gate, and only to Malav's keys (brief, hooks, package).
The edit is recorded as if the gated step had written it, so the graph resumes from the same gate.
"""
from __future__ import annotations

import base64
import re
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


@router.post("/runs/{cid}/edit")
def edit(cid: str, body: Edit):
    if body.key not in EDITABLE:
        raise HTTPException(403, f"'{body.key}' isn't editable here (Malav's keys: {', '.join(EDITABLE)})")
    cfg = {"configurable": {"thread_id": cid}}
    snap = _graph.get_state(cfg)
    gate = next((n for n in snap.next if n.endswith("__gate")), None)
    if not gate:
        raise HTTPException(409, "Edits apply while the run waits at an approval gate")
    try:
        if body.key == "hooks":
            value = [HookPlus.model_validate(h).model_dump(mode="json") for h in body.value]
        else:
            value = EDITABLE[body.key].model_validate(body.value).model_dump(mode="json")
    except Exception as e:
        raise HTTPException(422, f"Invalid {body.key}: {e}")
    if body.key == "brief":
        value["client_id"] = cid
    _graph.update_state(cfg, {body.key: value}, as_node=gate.removesuffix("__gate"))
    _graph.invoke(None, cfg)  # re-enter the gate so its approval card is pending again (it interrupts at once)
    bus.log(cid, "malav", f"You edited the {body.key} by hand")
    return {"ok": True}
