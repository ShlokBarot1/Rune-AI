"""HTTP API for the dashboard. Shared file: to add endpoints, create an APIRouter in your own package
(malav/api.py, shlok/api.py) and include it below.

Run: uvicorn api:app --reload --port 8000   (from backend/)
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from core import bus
from core.schemas import new_id, now
from graph import build
from malav import api as malav_api
from shlok import api as shlok_api

app = FastAPI(title="Rune AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
for d in ("media", "fixtures/media"):  # creative previews only — never mount the backend root (.env lives there)
    Path(d).mkdir(parents=True, exist_ok=True)
    app.mount(f"/files/{d}", StaticFiles(directory=d), name=d)

graph = build(SqliteSaver(sqlite3.connect(os.getenv("RUNE_CHECKPOINTS", "checkpoints.db"), check_same_thread=False)))
_running: set[str] = set()
_lock = threading.Lock()


def cfg(cid: str) -> dict:
    return {"configurable": {"thread_id": cid}}


def _runs_db():
    con = bus.connect()
    con.execute("create table if not exists runs (client_id text primary key, created_at text)")
    return con


def _run(cid: str, inp):
    try:
        graph.invoke(inp, cfg(cid))
    except Exception as e:
        bus.say(cid, "core", "human", "INFORM", {"error": f"{e.__class__.__name__}: {e}"})
    finally:
        _running.discard(cid)


def start(cid: str, inp):
    with _lock:
        if cid in _running:
            raise HTTPException(409, "This client's pipeline is already running")
        _running.add(cid)
    threading.Thread(target=_run, args=(cid, inp), daemon=True).start()


def snapshot(cid: str) -> dict:
    s = graph.get_state(cfg(cid))
    pending = [{**i.value, "interrupt_id": i.id} for t in s.tasks for i in t.interrupts]
    return {"client_id": cid, "values": s.values, "next": list(s.next), "pending": pending,
            "running": cid in _running}


def run_ids() -> list[str]:
    with _runs_db() as con:
        return [r["client_id"] for r in con.execute("select client_id from runs order by created_at desc")]


class StartRun(BaseModel):
    client_id: Optional[str] = None
    intake: dict = {}


class Resume(BaseModel):
    action: Optional[str] = None     # approve / changes / reject (gates)
    comment: str = ""
    answer: Optional[str] = None     # agent questions


@app.post("/runs")
def create_run(body: StartRun):
    cid = body.client_id or new_id("client")
    with _runs_db() as con:
        con.execute("insert or ignore into runs values (?,?)", (cid, now().isoformat()))
    start(cid, {"client_id": cid, "intake": body.intake})
    return {"client_id": cid}


@app.get("/runs")
def list_runs():
    out = []
    for cid in run_ids():
        s = snapshot(cid)
        v = s["values"]
        out.append({"client_id": cid, "name": (v.get("brief") or {}).get("business_name", cid), "next": s["next"],
                    "pending": len(s["pending"]), "running": s["running"], "live": bool(v.get("live")),
                    "audit": (v.get("audit") or {}).get("decision")})
    return out


@app.get("/runs/{cid}")
def get_run(cid: str):
    return snapshot(cid)


@app.post("/runs/{cid}/resume")
def resume(cid: str, body: Resume):
    if not snapshot(cid)["pending"]:
        raise HTTPException(409, "Nothing is waiting for a decision")
    value = body.answer if body.answer is not None else {"action": body.action or "approve", "comment": body.comment}
    start(cid, Command(resume=value))
    return {"ok": True}


@app.get("/approvals")
def approvals():
    out = []
    for cid in run_ids():
        s = snapshot(cid)
        name = (s["values"].get("brief") or {}).get("business_name", cid)
        out += [{"client_id": cid, "client": name, **p} for p in s["pending"]]
    return out


@app.get("/events")
async def events(client_id: Optional[str] = None, after: int = 0):
    """Agent bus as Server-Sent Events (Agent Theater, activity feed)."""
    async def gen():
        seq = after
        while True:
            with bus.connect() as con:
                q = "select * from agent_messages where seq>?" + (" and client_id=?" if client_id else "") + " order by seq limit 200"
                rows = con.execute(q, (seq, client_id) if client_id else (seq,)).fetchall()
            for r in rows:
                seq = r["seq"]
                yield {"event": "message", "data": json.dumps({**dict(r), "payload": json.loads(r["payload"])})}
            await asyncio.sleep(1)
    return EventSourceResponse(gen())


shlok_api.bind(graph)
shlok_api.start_scheduler(run_ids)
app.include_router(shlok_api.router)
malav_api.bind(graph)
app.include_router(malav_api.router)
