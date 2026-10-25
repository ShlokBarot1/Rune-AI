"""Accounts for agency use: who may see and do what, the agency's branding, signed share links.

Off unless RUNE_AUTH=1 (local dev and the tests run open). Roles:
  owner       everything, plus users and branding
  strategist  runs campaigns; `clients` limits which ones ([] = all)
  client      sees only their own campaigns (`clients`) and may approve, request changes and decide optimiser
              actions; never edits budgets, starts runs or stops agents

Passwords: PBKDF2-SHA256 (stdlib), 200k rounds. Sessions: random tokens, 30 days.
ponytail: SQLite tables in RUNE_DB; an IdP (Clerk / Auth0 / Google sign-in) replaces login() when needed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Literal, Optional

from pydantic import BaseModel

from . import bus

Role = Literal["owner", "strategist", "client"]
SESSION_S = 30 * 86400


class User(BaseModel):
    id: str
    email: str
    name: str
    role: Role
    clients: list[str] = []


def enabled() -> bool:
    return os.getenv("RUNE_AUTH") == "1"


def _db():
    con = bus.connect()
    con.execute("create table if not exists users (id text primary key, email text unique, name text, role text,"
                " pw text, salt text, clients text)")
    con.execute("create table if not exists sessions (token text primary key, user_id text, expires real)")
    con.execute("create table if not exists settings (key text primary key, value text)")
    return con


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def _user(row) -> User:
    return User(id=row["id"], email=row["email"], name=row["name"], role=row["role"], clients=json.loads(row["clients"]))


def setup_needed() -> bool:
    with _db() as con:
        return con.execute("select count(*) from users").fetchone()[0] == 0


def create_user(email: str, name: str, role: Role, password: str, clients: Optional[list[str]] = None) -> User:
    if len(password) < 8:
        raise ValueError("Password needs at least 8 characters")
    salt = secrets.token_hex(16)
    uid = "u_" + secrets.token_hex(6)
    with _db() as con:
        con.execute("insert into users values (?,?,?,?,?,?,?)",
                    (uid, email.strip().lower(), name, role, _hash(password, salt), salt, json.dumps(clients or [])))
    return User(id=uid, email=email.strip().lower(), name=name, role=role, clients=clients or [])


def users() -> list[User]:
    with _db() as con:
        return [_user(r) for r in con.execute("select * from users order by role, email")]


def update_user(uid: str, clients: Optional[list[str]] = None, role: Optional[Role] = None) -> None:
    with _db() as con:
        if clients is not None:
            con.execute("update users set clients=? where id=?", (json.dumps(clients), uid))
        if role:
            con.execute("update users set role=? where id=?", (role, uid))


def delete_user(uid: str) -> None:
    with _db() as con:
        con.execute("delete from users where id=?", (uid,))
        con.execute("delete from sessions where user_id=?", (uid,))


def login(email: str, password: str) -> Optional[str]:
    with _db() as con:
        row = con.execute("select * from users where email=?", (email.strip().lower(),)).fetchone()
        if not row or not hmac.compare_digest(row["pw"], _hash(password, row["salt"])):
            return None
        token = secrets.token_urlsafe(32)
        con.execute("insert into sessions values (?,?,?)", (token, row["id"], time.time() + SESSION_S))
    return token


def logout(token: str) -> None:
    with _db() as con:
        con.execute("delete from sessions where token=?", (token,))


def user_for(token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    with _db() as con:
        row = con.execute("select u.* from sessions s join users u on u.id=s.user_id where s.token=? and s.expires>?",
                          (token, time.time())).fetchone()
    return _user(row) if row else None


def can_see(user: User, cid: str) -> bool:
    return user.role == "owner" or (user.role == "strategist" and not user.clients) or cid in user.clients


# ---------- workspace settings: branding, the signing secret ----------

def setting(key: str, default=None):
    with _db() as con:
        row = con.execute("select value from settings where key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    with _db() as con:
        con.execute("insert or replace into settings values (?,?)", (key, json.dumps(value)))


def branding() -> dict:
    """White-label: what the dashboard, client portal and reports are branded as."""
    return {"agency_name": "Rune Studio", "logo": None, "color": "#D4F34A"} | (setting("branding") or {})


def _secret() -> bytes:
    s = os.getenv("RUNE_SECRET") or setting("secret")
    if not s:
        s = secrets.token_hex(32)
        set_setting("secret", s)
    return s.encode()


def share_token(cid: str, days: int, valid_days: int = 30) -> str:
    """A link anyone can open to see one report, nothing else; expires."""
    body = f"{cid}|{days}|{int(time.time() + valid_days * 86400)}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{body}|{sig}".encode()).decode().rstrip("=")


def read_share(token: str) -> Optional[tuple[str, int]]:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        cid, days, exp, sig = raw.rsplit("|", 3)
    except Exception:
        return None
    good = hmac.new(_secret(), f"{cid}|{days}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, good) or float(exp) < time.time():
        return None
    return cid, int(days)
