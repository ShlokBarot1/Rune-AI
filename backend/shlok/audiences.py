"""Custom audiences: who to retarget, who to keep out, who to find more of.

plan(brief) decides which audiences this client can have (it depends on what's connected: a Pixel, a Page, an
Instagram account, an uploaded customer list); ensure(brief) creates them in the ad account (idempotent through
launch's meta_objects table) and returns {key: audience id}; targeting.build_spec uses the ids.

  visitors    site visitors in the last N days (Pixel)           -> the retargeting ad set
  engagers    people who engaged with the Page / Instagram        -> the retargeting ad set
  purchasers  Pixel purchasers, 180 days                          -> excluded from prospecting
  customers   the client's customer list (hashed emails / phones) -> excluded from prospecting
  lookalike   N% lookalike of customers (else purchasers)         -> the first prospecting ad set

Customer data never leaves the server unhashed: emails / phones are normalised and SHA-256 hashed first.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re

from core import bus
from core.schemas import ClientBrief, MetaObject

from . import meta

AGENT = "shlok.targeting"
SPECIAL = ("credit", "employment", "housing", "social_issues")


def connected() -> dict[str, str]:
    return {k: v for k, v in {"pixel": os.getenv("META_PIXEL_ID", ""), "page": os.getenv("META_PAGE_ID", ""),
                              "ig": os.getenv("META_IG_ACCOUNT_ID", "")}.items() if v and v != "PAGE_ID"}


def _rule(source_id: str, source: str, days: int, event: str = "") -> dict:
    flt = ({"field": "event", "operator": "eq", "value": event} if event
           else {"field": "url", "operator": "i_contains", "value": ""})
    return {"inclusions": {"operator": "or", "rules": [{
        "event_sources": [{"id": source_id, "type": source}], "retention_seconds": days * 86400,
        "filter": {"operator": "and", "filters": [flt]}}]}}


def plan(brief: ClientBrief) -> dict[str, dict]:
    """key -> audience payload (without account / origin ids filled in). Empty when nothing is connected."""
    a, ids, name = brief.audiences, connected(), brief.business_name
    out: dict[str, dict] = {}
    if a.retarget and ids.get("pixel"):
        out["visitors"] = {"name": f"{name} · site visitors {a.retarget_days}d",
                           "rule": _rule(ids["pixel"], "pixel", a.retarget_days)}
    if a.retarget and (ids.get("page") or ids.get("ig")):
        rules = []
        if ids.get("page"):
            rules += _rule(ids["page"], "page", 365, "page_engaged")["inclusions"]["rules"]
        if ids.get("ig"):
            rules += _rule(ids["ig"], "ig_business", 365, "ig_business_profile_engaged")["inclusions"]["rules"]
        out["engagers"] = {"name": f"{name} · Page & Instagram engagers 365d",
                           "rule": {"inclusions": {"operator": "or", "rules": rules}}}
    if a.exclude_buyers and ids.get("pixel"):
        out["purchasers"] = {"name": f"{name} · purchasers 180d", "rule": _rule(ids["pixel"], "pixel", 180, "Purchase")}
    if a.customer_list:
        out["customers"] = {"name": f"{name} · customer list", "subtype": "CUSTOM",
                            "customer_file_source": "USER_PROVIDED_ONLY"}
    seed = "customers" if "customers" in out else "purchasers" if "purchasers" in out else ""
    if a.lookalike_pct and seed and brief.special_ad_category not in SPECIAL:
        out["lookalike"] = {"name": f"{name} · {a.lookalike_pct}% lookalike", "subtype": "LOOKALIKE", "origin": seed,
                            "lookalike_spec": {"ratio": a.lookalike_pct / 100, "country": brief.geo[0]}}
    return out


def _norm(value: str) -> tuple[str, str] | None:
    """('EMAIL_SHA256' | 'PHONE_SHA256', hash) per Meta's normalisation rules; None for junk."""
    v = value.strip().lower()
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", v):
        return "EMAIL_SHA256", hashlib.sha256(v.encode()).hexdigest()
    digits = re.sub(r"\D", "", v)
    if 8 <= len(digits) <= 15:
        return "PHONE_SHA256", hashlib.sha256(digits.encode()).hexdigest()
    return None


def hashed_rows(path: str) -> dict[str, list[str]]:
    """The customer CSV as hashes per schema; any column holding emails or phone numbers counts."""
    out: dict[str, list[str]] = {"EMAIL_SHA256": [], "PHONE_SHA256": []}
    from malav.media import resolve
    with open(resolve(path), newline="", encoding="utf-8-sig", errors="replace") as f:
        for row in csv.reader(f):
            for cell in row:
                if n := _norm(cell):
                    out[n[0]].append(n[1])
    return {k: list(dict.fromkeys(v)) for k, v in out.items() if v}


def ensure(brief: ClientBrief) -> dict[str, str]:
    """Create (or reuse) this client's audiences; {key: id}. Dry-run ids look like dry_aud_<key>. Never raises:
    an audience Meta refuses (e.g. terms not accepted) is skipped and logged, targeting then goes without it."""
    from .launch import _save, existing
    cid, wanted = brief.client_id, plan(brief)
    have = {} if meta.dry_run() else existing(cid)
    ids: dict[str, str] = {}
    for key in [k for k in wanted if k != "lookalike"] + (["lookalike"] if "lookalike" in wanted else []):
        spec = dict(wanted[key])
        ref = f"audience:{key}"
        if ref in have:
            ids[key] = have[ref].meta_id
            continue
        if meta.dry_run():
            ids[key] = f"dry_aud_{key}"
            continue
        try:
            payload = {k: v for k, v in spec.items() if k != "origin"}
            if "rule" in payload:
                payload["rule"] = json.dumps(payload["rule"])
            if key == "lookalike":
                if spec["origin"] not in ids:
                    continue
                payload["origin_audience_id"] = ids[spec["origin"]]
            aid = meta.api("POST", f"{meta.account()}/customaudiences", data=payload)["id"]
            if key == "customers":
                for schema, hashes in hashed_rows(brief.audiences.customer_list).items():
                    for i in range(0, len(hashes), 10000):  # Meta takes 10k rows per call
                        meta.api("POST", f"{aid}/users", data={"payload": {"schema": schema,
                                                                            "data": [[h] for h in hashes[i:i + 10000]]}})
            _save(cid, MetaObject(local_ref=ref, kind="audience", meta_id=aid, status="ACTIVE"))
            ids[key] = aid
        except Exception as e:
            bus.log(cid, AGENT, f"Couldn't create the {spec['name']} audience ({str(e)[:140]}); going without it")
    if ids:
        bus.log(cid, AGENT, "Audiences ready: " + ", ".join(wanted[k]["name"] for k in ids))
    return ids


def describe(brief: ClientBrief) -> str:
    """One line for the strategy log and the approval card."""
    p = plan(brief)
    if not p:
        return "No Pixel, Page or customer list connected: interest targeting only, no retargeting or exclusions"
    return "Audiences: " + ", ".join(v["name"].split(" · ", 1)[1] for v in p.values())


def can_retarget(brief: ClientBrief) -> bool:
    return bool({"visitors", "engagers"} & set(plan(brief)))
