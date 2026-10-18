"""Thin Meta Graph API client. Dry-run when META_DRY_RUN=1 or no token: callers record payloads instead."""
from __future__ import annotations

import base64
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx


class MetaError(RuntimeError):
    pass


def dry_run() -> bool:
    return os.getenv("META_DRY_RUN", "1") == "1" or not os.getenv("META_ACCESS_TOKEN")


def account() -> str:
    return os.getenv("META_AD_ACCOUNT_ID", "act_0")


def _url(path: str) -> str:
    return f"https://graph.facebook.com/{os.getenv('META_API_VERSION', 'v23.0')}/{path}"


def _form(data: dict) -> dict:
    """Graph API form fields: nested objects/lists must be JSON strings."""
    return {k: json.dumps(v) if isinstance(v, (dict, list, bool)) else v for k, v in data.items()}


def api(method: str, path: str, data: Optional[dict] = None, params: Optional[dict] = None,
        files: Optional[dict] = None, timeout: float = 120) -> dict:
    p = {**(params or {}), "access_token": os.environ["META_ACCESS_TOKEN"]}
    for attempt in range(3):
        r = httpx.request(method, _url(path), params=p, data=_form(data or {}), files=files, timeout=timeout)
        body = r.json() if r.content else {}
        err = body.get("error") if isinstance(body, dict) else None
        # 17/80004 = rate limited: back off and retry
        if err and err.get("code") in (17, 80004) and attempt < 2:
            time.sleep(30 * (attempt + 1))
            continue
        if r.status_code >= 400 or err:
            raise MetaError(f"{method} {path}: {err.get('message') if err else r.text} "
                            f"{(err or {}).get('error_user_msg', '')}".strip())
        return body
    raise MetaError(f"{method} {path}: rate limited")


@lru_cache(maxsize=1)
def account_facts() -> dict:
    return api("GET", account(), params={"fields": "currency,timezone_name,min_daily_budget,account_status"})


def search_interests(q: str, limit: int = 2) -> list[dict]:
    data = api("GET", "search", params={"type": "adinterest", "q": q, "limit": limit}).get("data", [])
    return [{"id": d["id"], "name": d["name"]} for d in data]


def upload_image(path: str) -> str:
    body = api("POST", f"{account()}/adimages", data={"bytes": base64.b64encode(Path(path).read_bytes()).decode()})
    return next(iter(body["images"].values()))["hash"]


def check() -> None:
    """`python -m shlok.meta` — verify the token, ad account and page from .env."""
    me = api("GET", "me", params={"fields": "id,name"})
    print(f"token ok: {me['name']} ({me['id']})")
    perms = [p["permission"] for p in api("GET", "me/permissions")["data"] if p["status"] == "granted"]
    missing = {"ads_management", "ads_read", "pages_manage_ads"} - set(perms)
    print("permissions:", ", ".join(perms), f"| MISSING: {missing}" if missing else "")
    facts = account_facts()
    print(f"ad account {account()}: currency {facts['currency']}, min daily budget "
          f"{int(facts['min_daily_budget']) / 100}, status {facts['account_status']}")
    if not os.getenv("META_PAGE_ID"):
        print("page: META_PAGE_ID not set — needed to create ad creatives")
        return
    pages = {p["id"]: p["name"] for p in api("GET", f"{account()}/promote_pages", params={"fields": "id,name"})["data"]}
    pid = os.environ["META_PAGE_ID"]
    print(f"page ok: {pages[pid]}" if pid in pages else
          f"page {pid}: not listed in promote_pages (fine for sandbox if creatives still build)")


def upload_video(path: str, wait_s: int = 300) -> str:
    with open(path, "rb") as f:
        vid = api("POST", f"{account()}/advideos", files={"source": (Path(path).name, f, "video/mp4")}, timeout=600)["id"]
    deadline = time.time() + wait_s
    while time.time() < deadline:
        status = api("GET", vid, params={"fields": "status"}).get("status", {}).get("video_status")
        if status == "ready":
            return vid
        if status == "error":
            raise MetaError(f"video {path} failed processing")
        time.sleep(5)
    raise MetaError(f"video {path} not ready after {wait_s}s")


if __name__ == "__main__":
    import core  # noqa: F401  (loads .env)
    check()
