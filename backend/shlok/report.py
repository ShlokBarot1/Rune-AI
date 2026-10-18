"""S8 Reporting Agent: numbers computed in code, the LLM only writes the plain-language summary."""
from __future__ import annotations

from core import bus, llm, skills
from core.schemas import BudgetPlan, ClientBrief


def totals(stats: dict[str, dict]) -> dict:
    spend = sum(s["spend"] for s in stats.values())
    conv = sum(s["conversions"] for s in stats.values())
    rev = sum(s["revenue"] for s in stats.values())
    impr = sum(s["impressions"] for s in stats.values())
    clicks = sum(s["clicks"] for s in stats.values())
    return {"spend": round(spend, 2), "conversions": conv, "revenue": round(rev, 2), "impressions": impr,
            "clicks": clicks, "ctr": clicks / impr if impr else 0, "cpa": spend / conv if conv else None,
            "roas": rev / spend if spend else 0}


def daily_report(brief: ClientBrief, plan: BudgetPlan, stats: dict[str, dict], actions: list[dict]) -> str:
    t = totals(stats)
    best = sorted(stats.items(), key=lambda x: (x[1]["cpa"] is None, x[1]["cpa"] or 0))[:3]
    cur = plan.currency
    facts = (
        f"Spend {t['spend']:,.0f} {cur} of {plan.total:,.0f} budget. {t['conversions']} conversions, "
        f"CPA {t['cpa'] or 0:,.0f} (target {plan.target_cpa or brief.kpi_targets.target_cpa or '-'}), "
        f"ROAS {t['roas']:.2f}, CTR {t['ctr']:.2%}.\n"
        "Best ads: " + "; ".join(f"{r} CPA {s['cpa'] or 0:,.0f}" for r, s in best) + "\n"
        "Actions: " + ("; ".join(f"{a['kind']} {a['ref']} ({a['status']}): {a['reason']}" for a in actions) or "none")
    )
    try:
        text = llm.chat("fast", skills.with_skills(
            "Write a short daily ad performance report for a busy client: 3 short sections "
            "(How it's going, What we changed, Next). Use only the given numbers.", ["analytics"]), facts)
    except Exception as e:  # report still useful without the LLM
        bus.log(brief.client_id, "shlok.report", f"LLM unavailable ({e.__class__.__name__}); plain report")
        text = facts
    bus.say(brief.client_id, "shlok.report", "human", "INFORM", {"report": text, "totals": t})
    return text
