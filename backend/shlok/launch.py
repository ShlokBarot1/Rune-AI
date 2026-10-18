"""S4 Meta Launch Agent: builds campaign -> ad sets -> creatives -> ads, everything PAUSED.

Idempotent (meta_objects table maps local refs to Meta IDs, reruns skip what exists), rolls back what
it created in a failed run, and in dry-run returns the exact payloads without calling Meta.
One ad per creative. ponytail: placement asset customisation (one ad serving 4:5 in feed + 9:16 in
reels via asset_feed_spec) is the upgrade if the per-ratio ads fragment delivery.
"""
from __future__ import annotations

import os
from datetime import datetime, time, timezone

from core import bus
from core.bus import connect
from core.schemas import (BudgetPlan, ClientBrief, CreativePackage, LaunchResult, MetaObject, TargetingSpec)

from . import meta

SPECIAL = {"none": [], "credit": ["FINANCIAL_PRODUCTS_SERVICES"], "employment": ["EMPLOYMENT"],
           "housing": ["HOUSING"], "social_issues": ["ISSUES_ELECTIONS_POLITICS"]}
OPTIMIZATION = {"OUTCOME_SALES": ("OFFSITE_CONVERSIONS", "PURCHASE"), "OUTCOME_LEADS": ("OFFSITE_CONVERSIONS", "LEAD"),
                "OUTCOME_TRAFFIC": ("LINK_CLICKS", None), "OUTCOME_AWARENESS": ("REACH", None),
                "OUTCOME_ENGAGEMENT": ("POST_ENGAGEMENT", None)}


def _db():
    con = connect()
    con.execute("create table if not exists meta_objects (client_id text, local_ref text, kind text, meta_id text,"
                " status text, primary key (client_id, local_ref))")
    return con


def existing(client_id: str) -> dict[str, MetaObject]:
    with _db() as con:
        rows = con.execute("select * from meta_objects where client_id=?", (client_id,)).fetchall()
    return {r["local_ref"]: MetaObject(local_ref=r["local_ref"], kind=r["kind"], meta_id=r["meta_id"], status=r["status"]) for r in rows}


def _save(client_id: str, o: MetaObject):
    with _db() as con:
        con.execute("insert or replace into meta_objects values (?,?,?,?,?)", (client_id, o.local_ref, o.kind, o.meta_id, o.status))


def _forget(client_id: str, local_ref: str):
    with _db() as con:
        con.execute("delete from meta_objects where client_id=? and local_ref=?", (client_id, local_ref))


def _iso(d, end=False) -> str:
    return datetime.combine(d, time(23, 59) if end else time(0, 0), tzinfo=timezone.utc).isoformat()


def build(brief: ClientBrief, pkg: CreativePackage, plan: BudgetPlan, targeting: list[TargetingSpec]) -> LaunchResult:
    cid = brief.client_id
    dry = meta.dry_run()
    acct, page, pixel = meta.account(), os.getenv("META_PAGE_ID", "PAGE_ID"), os.getenv("META_PIXEL_ID")
    have = {} if dry else existing(cid)
    result = LaunchResult(dry_run=dry)
    created: list[MetaObject] = []
    tmap = {t.adset_ref: t for t in targeting}

    referenced: set[str] = set()

    def make(local_ref: str, kind: str, path: str, payload: dict, upload=None) -> str:
        referenced.add(local_ref)
        if local_ref in have:
            result.objects.append(have[local_ref])
            return have[local_ref].meta_id
        result.payloads.append({"local_ref": local_ref, "POST": path, **payload})
        if dry:
            meta_id = f"dry_{local_ref}"
        else:
            meta_id = upload() if upload else meta.api("POST", path, data=payload)["id"]
            obj = MetaObject(local_ref=local_ref, kind=kind, meta_id=meta_id, status="PAUSED")
            _save(cid, obj)
            created.append(obj)
        o = MetaObject(local_ref=local_ref, kind=kind, meta_id=meta_id, status="PAUSED")
        result.objects.append(o)
        return meta_id

    bus.log(cid, "shlok.launch", f"Building on Meta ({'DRY RUN' if dry else acct}), everything PAUSED")
    try:
        for camp in pkg.blueprint.campaigns:
            objective = camp.objective
            goal, event = OPTIMIZATION.get(objective, ("LINK_CLICKS", None))
            if event and not pixel:
                bus.log(cid, "shlok.launch", f"No META_PIXEL_ID: {camp.name} falls back to OUTCOME_TRAFFIC / LINK_CLICKS")
                objective, goal, event = "OUTCOME_TRAFFIC", "LINK_CLICKS", None
            camp_id = make(f"campaign:{camp.ref}", "campaign", f"{acct}/campaigns", {
                "name": camp.name, "objective": objective, "status": "PAUSED",
                "special_ad_categories": SPECIAL[brief.special_ad_category],
                "is_adset_budget_sharing_enabled": False,
            })
            for s in camp.adsets:
                adset_payload = {
                    "name": s.name, "campaign_id": camp_id, "status": "PAUSED",
                    "daily_budget": int(round(plan.adset_daily[s.ref] * 100)),  # minor units
                    "billing_event": "IMPRESSIONS", "optimization_goal": goal, "bid_strategy": plan.bid_strategy,
                    "targeting": tmap[s.ref].spec,
                    "start_time": _iso(brief.budget.start), "end_time": _iso(brief.budget.end, end=True),
                }
                if event:
                    adset_payload["promoted_object"] = {"pixel_id": pixel, "custom_event_type": event}
                adset_id = make(f"adset:{s.ref}", "adset", f"{acct}/adsets", adset_payload)

                for c in (c for c in pkg.creatives if c.adset_ref == s.ref):
                    cta = {"type": c.ad_copy.cta, "value": {"link": c.landing_url}}
                    if c.kind == "image":
                        h = make(f"image:{c.creative_id}", "image", f"{acct}/adimages", {"file": c.path},
                                 upload=lambda p=c.path: meta.upload_image(p))
                        story = {"page_id": page, "link_data": {
                            "image_hash": h, "link": c.landing_url, "message": c.ad_copy.primary_text,
                            "name": c.ad_copy.headline, "description": c.ad_copy.description, "call_to_action": cta}}
                    else:
                        v = make(f"video:{c.creative_id}", "video", f"{acct}/advideos", {"file": c.path},
                                 upload=lambda p=c.path: meta.upload_video(p))
                        thumb = make(f"image:{c.creative_id}:thumb", "image", f"{acct}/adimages",
                                     {"file": c.thumbnail_path}, upload=lambda p=c.thumbnail_path: meta.upload_image(p))
                        story = {"page_id": page, "video_data": {
                            "video_id": v, "image_hash": thumb, "message": c.ad_copy.primary_text,
                            "title": c.ad_copy.headline, "link_description": c.ad_copy.description, "call_to_action": cta}}
                    cr_id = make(f"creative:{c.creative_id}", "creative", f"{acct}/adcreatives",
                                 {"name": c.creative_id, "object_story_spec": story})
                    make(f"ad:{c.creative_id}", "ad", f"{acct}/ads",
                         {"name": f"{s.name} · {c.ratio} · v{c.version}", "adset_id": adset_id,
                          "creative": {"creative_id": cr_id}, "status": "PAUSED"})
    except Exception as e:
        bus.log(cid, "shlok.launch", f"Launch failed: {e}. Rolling back {len(created)} new objects.")
        for o in reversed(created):
            if o.kind not in ("image",):  # image hashes can't be deleted by id; harmless leftovers
                try:
                    meta.api("DELETE", o.meta_id)
                except meta.MetaError:
                    pass
            _forget(cid, o.local_ref)
        raise
    for ref, o in have.items():  # creatives replaced by a fix round: remove their old ads
        if o.kind == "ad" and ref not in referenced:
            meta.api("DELETE", o.meta_id)
            _forget(cid, ref)
            bus.log(cid, "shlok.launch", f"Removed superseded ad {ref}")
    bus.log(cid, "shlok.launch", f"Built {sum(o.kind == 'ad' for o in result.objects)} ads, all PAUSED")
    return result


def set_status(client_id: str, result: LaunchResult, status: str, kinds=("campaign", "adset", "ad")) -> LaunchResult:
    for o in result.objects:
        if o.kind in kinds:
            if not result.dry_run:
                meta.api("POST", o.meta_id, data={"status": status})
                _save(client_id, o.model_copy(update={"status": status}))
            o.status = status
    return result
