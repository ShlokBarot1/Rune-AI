"""Performance Simulator: sandbox ad accounts never deliver, so this generates realistic daily
insights per ad (a few clear winners/losers, some creatives fatigue) to drive the optimise loop in demos.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from core.schemas import BudgetPlan, ClientBrief, CreativePackage, InsightRow


def simulate(brief: ClientBrief, pkg: CreativePackage, plan: BudgetPlan, days: int = 7, seed: int = 7,
             start_day: int = 0, active: set[str] | None = None) -> list[InsightRow]:
    """active: creative ids whose Meta ad is ACTIVE (paused or awaiting-approval ads don't deliver); None = all."""
    rnd = random.Random(seed)
    price = (brief.products[0].price if brief.products and brief.products[0].price else 500)
    start = datetime.combine(brief.budget.start, datetime.min.time(), tzinfo=timezone.utc)
    rows: list[InsightRow] = []
    plans = {s.ref: s for s in pkg.blueprint.adsets()}
    for adset_ref, daily in plan.adset_daily.items():
        s = plans.get(adset_ref)
        src = s.reuse_from if s and s.reuse_from else adset_ref  # retargeting runs another ad set's creatives
        suffix = f"@{adset_ref}" if src != adset_ref else ""
        ads = [(c.creative_id + suffix, c) for c in pkg.creatives
               if c.adset_ref == src and (active is None or c.creative_id + suffix in active)]
        warm = 2.5 if s and s.audience == "retargeting" else 1.0  # people who already visited convert more
        for ad_id, c in ads:
            # per-creative "true" quality, stable for a given seed + creative
            q = random.Random(f"{seed}:{ad_id}")
            base_ctr = q.lognormvariate(math.log(0.012), 0.5)
            cvr = q.uniform(0.01, 0.06) * warm
            cpm = q.uniform(90, 180)
            fatigues = q.random() < 0.4
            hook_rate = q.uniform(0.1, 0.4)
            for d in range(start_day, start_day + days):
                spend = daily / len(ads) * rnd.uniform(0.8, 1.2)
                impressions = int(spend / cpm * 1000)
                freq = 1 + (0.3 if fatigues else 0.1) * d
                ctr = base_ctr * (0.9 ** max(0, d - 2) if fatigues else 1) * rnd.uniform(0.85, 1.15)
                clicks = int(impressions * ctr)
                conv = int(round(clicks * cvr * rnd.uniform(0.7, 1.3)))
                v3 = int(impressions * hook_rate) if c.kind == "video" else 0
                rows.append(InsightRow(
                    ts=start + timedelta(days=d), ad_ref=ad_id, adset_ref=adset_ref, spend=round(spend, 2),
                    impressions=impressions, clicks=clicks, conversions=conv, revenue=conv * price,
                    video_3s=v3, thruplays=int(v3 * q.uniform(0.2, 0.5)), frequency=round(freq, 2), source="simulated"))
    return rows
