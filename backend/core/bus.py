"""Agent bus: every inter-agent message and agent log line lands in one SQLite table.

The UI's Agent Theater streams this table. Cross-team calls are plain Python
function calls (fast, typed); the callee also records REQUEST/RESPONSE here so
the conversation is visible.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

from .schemas import AgentMessage

# Clients the human asked to stop. Every agent posts here constantly, so the next post of a stopped run
# raises and unwinds the graph; BaseException so the agents' `except Exception` fallbacks don't swallow it.
# ponytail: a step stuck inside one long LLM/HTTP call stops only once that call returns.
STOPPED: set[str] = set()


class Stopped(BaseException):
    pass


_READY: set[str] = set()  # DB files whose schema/journal setup already ran in this process


def connect() -> sqlite3.Connection:
    """Shared SQLite DB (env RUNE_DB). Each owner creates their own tables with `create table if not exists`."""
    path = os.getenv("RUNE_DB", "rune.db")
    # timeout: research and creatives log from several threads at once; wait for the writer instead of failing
    con = sqlite3.connect(path, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    if path not in _READY:
        con.execute("pragma journal_mode=wal")  # readers (SSE, polling) never block the agents' writes
        con.execute(
            "create table if not exists agent_messages (seq integer primary key autoincrement, id text,"
            " thread_id text, client_id text, sender text, recipient text, type text, payload text, created_at text)"
        )
        con.execute("create index if not exists agent_messages_client on agent_messages (client_id, seq)")
        _READY.add(path)
    return con


def say(client_id: str, sender: str, recipient: str, type: str, payload: Optional[dict] = None,
        thread_id: Optional[str] = None) -> AgentMessage:
    if client_id in STOPPED:
        raise Stopped(client_id)
    msg = AgentMessage(client_id=client_id, sender=sender, recipient=recipient, type=type,
                       payload=payload or {}, thread_id=thread_id or client_id)
    with connect() as con:
        con.execute(
            "insert into agent_messages (id, thread_id, client_id, sender, recipient, type, payload, created_at)"
            " values (?,?,?,?,?,?,?,?)",
            (msg.id, msg.thread_id, msg.client_id, msg.sender, msg.recipient, msg.type,
             json.dumps(msg.payload, default=str), msg.created_at.isoformat()),
        )
    return msg


def log(client_id: str, agent: str, text: str, **data) -> AgentMessage:
    """One step in an agent's work, shown live in Agent Theater."""
    return say(client_id, agent, "theater", "LOG", {"text": text, **data})


def last_seq(client_id: str) -> int:
    with connect() as con:
        return con.execute("select coalesce(max(seq), 0) from agent_messages where client_id=?", (client_id,)).fetchone()[0]


def history(client_id: str, after_seq: int = 0) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            "select * from agent_messages where client_id=? and seq>? order by seq", (client_id, after_seq)
        ).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
