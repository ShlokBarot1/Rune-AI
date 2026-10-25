"""What the client's ad account already knows: past ads ranked by results, a CPA benchmark, the copy that won.

A brand that has run Meta ads before shouldn't be planned from zero. research_step folds this into the research
report (summary + angles) and budget_step offers the historic CPA as the target. None when not connected.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Optional

from core import bus

from . import meta

CONVERSIONS = ("purchase", "offsite_conversion.fb_pixel_purchase", "lead", "offsite_conversion.fb_pixel_lead",
               "onsite_conversion.messaging_conversation_started_7d")


def _conv(row: dict) -> float:
    return sum(float(a["value"]) for a in row.get("actions") or [] if a["action_type"] in CONVERSIONS)


def account_learnings(cid: str, days: int = 180) -> Optional[dict]:
    """{"text": lines for the research report, "cpa": benchmark or None, "winners": [copy]}; None when the account
    isn't connected or has no history. Never raises."""
    if meta.dry_run():
        return None
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = meta.api("GET", f"{meta.account()}/insights", params={
            "level": "ad", "time_range": json.dumps({"since": since, "until": date.today().isoformat()}),
            "fields": "ad_id,ad_name,spend,impressions,clicks,ctr,actions", "limit": 200}).get("data", [])
    except Exception as e:
        bus.log(cid, "shlok.history", f"Couldn't read the ad account's history ({str(e)[:120]})")
        return None
    rows = [r for r in rows if float(r.get("spend", 0)) > 0]
    if not rows:
        return None
    spend, conv = sum(float(r["spend"]) for r in rows), sum(_conv(r) for r in rows)
    ranked = sorted((r for r in rows if _conv(r) >= 3), key=lambda r: float(r["spend"]) / _conv(r))
    winners = []
    for r in ranked[:3]:
        try:
            cr = meta.api("GET", r["ad_id"], params={"fields": "creative{body,title}"}).get("creative", {})
            winners.append(cr.get("body") or cr.get("title") or r["ad_name"])
        except Exception:
            winners.append(r["ad_name"])
    cpa = spend / conv if conv else None
    text = [f"Ad account history: {len(rows)} ads, {spend:,.0f} spent, {conv:,.0f} conversions"
            + (f", CPA {cpa:,.0f}" if cpa else "")]
    text += [f"Past winner (cheapest results): “{w[:160]}”" for w in winners]
    bus.log(cid, "shlok.history", text[0] + (f"; {len(winners)} proven ads to learn from" if winners else ""))
    return {"text": text, "cpa": cpa, "winners": winners}
