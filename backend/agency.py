"""Agency endpoints (included by api.py): sign-in, users and roles, white-label branding, client reports and share
links. Access rules live in guard() below, applied to every request when RUNE_AUTH=1 (see core.auth)."""
from __future__ import annotations

import re
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from core import auth

router = APIRouter()
_graph = None
OPEN = ("/auth/login", "/auth/setup", "/auth/status", "/share/", "/files/", "/settings/public", "/docs", "/openapi")
CLIENT_WRITES = re.compile(r"^/runs/[^/]+/(resume|actions/[^/]+|share)$")  # what a client may change


def bind(graph) -> None:
    global _graph
    _graph = graph


def _deny(code: int, msg: str) -> JSONResponse:  # with CORS headers: this answers before the CORS middleware
    return JSONResponse({"detail": msg}, status_code=code, headers={"Access-Control-Allow-Origin": "*"})


async def guard(request: Request, call_next):
    if not auth.enabled() or request.method == "OPTIONS" or request.url.path.startswith(OPEN):
        return await call_next(request)
    path = request.url.path
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("token")
    user = auth.user_for(token)
    if not user:
        return _deny(401, "Please sign in")
    m = re.match(r"^/(?:malav/)?runs/([^/]+)", path)
    if m and not auth.can_see(user, m.group(1)):
        return _deny(403, "This campaign isn't shared with you")
    if user.role == "client" and request.method != "GET" and not CLIENT_WRITES.match(path):
        return _deny(403, "Clients can approve, request changes and decide optimiser actions; ask your agency for the rest")
    if (path.startswith("/auth/users") or path == "/settings") and user.role != "owner" and request.method != "GET":
        return _deny(403, "Only the workspace owner can do that")
    if path == "/events" and not request.query_params.get("client_id") and not (
            user.role == "owner" or (user.role == "strategist" and not user.clients)):
        return _deny(403, "Pick a campaign")
    request.state.user = user
    return await call_next(request)


def visible(request: Request, cids: list[str]) -> list[str]:
    user = getattr(request.state, "user", None)
    return [c for c in cids if not user or auth.can_see(user, c)]


# ---------- sign-in ----------

class Login(BaseModel):
    email: str
    password: str


class NewUser(BaseModel):
    email: str
    name: str
    role: Literal["owner", "strategist", "client"]
    password: str
    clients: list[str] = []


@router.get("/auth/status")
def status():
    return {"enabled": auth.enabled(), "setup_needed": auth.enabled() and auth.setup_needed(), "branding": auth.branding()}


@router.post("/auth/setup")
def setup(body: NewUser):
    """The first account of a workspace; it is always the owner."""
    if not auth.setup_needed():
        raise HTTPException(409, "This workspace already has an owner")
    try:
        auth.create_user(body.email, body.name, "owner", body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"token": auth.login(body.email, body.password)}


@router.post("/auth/login")
def login(body: Login):
    token = auth.login(body.email, body.password)
    if not token:
        raise HTTPException(401, "Wrong email or password")
    return {"token": token, "user": auth.user_for(token).model_dump()}


@router.get("/auth/me")
def me(request: Request):
    u = getattr(request.state, "user", None)
    return {"user": u.model_dump() if u else None, "enabled": auth.enabled()}


@router.post("/auth/logout")
def logout(request: Request):
    auth.logout(request.headers.get("authorization", "").removeprefix("Bearer ").strip())
    return {"ok": True}


@router.get("/auth/users")
def list_users():
    return [u.model_dump() for u in auth.users()]


@router.post("/auth/users")
def add_user(body: NewUser):
    try:
        return auth.create_user(body.email, body.name, body.role, body.password, body.clients).model_dump()
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        raise HTTPException(409, "That email already has an account")


class UserPatch(BaseModel):
    clients: Optional[list[str]] = None
    role: Optional[Literal["owner", "strategist", "client"]] = None


@router.post("/auth/users/{uid}")
def edit_user(uid: str, body: UserPatch):
    auth.update_user(uid, body.clients, body.role)
    return {"ok": True}


@router.post("/auth/users/{uid}/delete")
def remove_user(uid: str, request: Request):
    if getattr(request.state, "user", None) and request.state.user.id == uid:
        raise HTTPException(409, "You can't delete your own account")
    auth.delete_user(uid)
    return {"ok": True}


# ---------- branding ----------

class Branding(BaseModel):
    agency_name: str
    color: str = "#D4F34A"
    logo: Optional[str] = None


@router.get("/settings/public")
def public_settings():
    return auth.branding()


@router.post("/settings")
def save_settings(body: Branding):
    auth.set_setting("branding", body.model_dump())
    return auth.branding()


# ---------- reports ----------

def _report(cid: str, days: int) -> str:
    from shlok.report import weekly_html
    v = _graph.get_state({"configurable": {"thread_id": cid}}).values
    if not v.get("budget_plan") or not v.get("package"):
        raise HTTPException(409, "There's nothing to report until the campaign is built")
    return weekly_html(v, days, auth.branding())


@router.get("/runs/{cid}/report.html", response_class=HTMLResponse)
def report(cid: str, days: int = 7):
    return _report(cid, days)


@router.post("/runs/{cid}/share")
def share(cid: str, days: int = 7):
    return {"path": f"/share/{auth.share_token(cid, days)}"}


@router.get("/share/{token}", response_class=HTMLResponse)
def shared(token: str):
    got = auth.read_share(token)
    if not got:
        raise HTTPException(404, "This link has expired or is wrong")
    return _report(*got)
