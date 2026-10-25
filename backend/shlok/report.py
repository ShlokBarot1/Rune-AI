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


ACTION_WORDS = {"pause_ad": "Paused", "scale_adset": "More budget for", "cut_adset": "Stopped", "refresh_creatives":
                "New creatives requested for", "launch_creatives": "New creatives ready for", "pause_campaign":
                "Paused everything", "fix_disapproved": "Fixing a disapproved ad"}


def daily_report(brief: ClientBrief, plan: BudgetPlan, stats: dict[str, dict], actions: list[dict],
                 names: dict[str, str] | None = None) -> str:
    """names: ad / ad set ref -> what a client calls it (headline, ad set name); never show internal ids."""
    names = names or {}
    said = lambda ref: names.get(ref.split("@")[0], "an ad") + (" (retargeting)" if "@" in ref else "")
    t = totals(stats)
    best = sorted(stats.items(), key=lambda x: (x[1]["cpa"] is None, x[1]["cpa"] or 0))[:3]
    cur = plan.currency
    target = plan.target_cpa or brief.kpi_targets.target_cpa
    # verdicts are decided here, not by the model (small models flip comparisons)
    verdict = ("no target set" if not target or not t["cpa"] else
               f"BETTER than the {target:,.0f} target (cheaper per conversion — good)" if t["cpa"] <= target else
               f"WORSE than the {target:,.0f} target (too expensive per conversion)")
    facts = (
        f"Spend {t['spend']:,.0f} {cur} of {plan.total:,.0f} budget. {t['conversions']} conversions.\n"
        f"Cost per conversion (CPA) {t['cpa'] or 0:,.0f} {cur}: {verdict}.\n"
        f"ROAS {t['roas']:.2f}, CTR {t['ctr']:.2%}.\n"
        "Best ads: " + "; ".join(f"“{said(r)}” CPA {s['cpa'] or 0:,.0f}" for r, s in best) + "\n"
        "Actions: " + ("; ".join(f"{ACTION_WORDS.get(a['kind'], a['kind'])} {said(a['ref'])} ({a['status']}): {a['reason']}"
                                 for a in actions) or "none")
    )
    try:
        text = llm.chat("fast", skills.with_skills(
            "Write a short daily ad performance report for a busy client: 3 short sections "
            "(How it's going, What we changed, Next). Use only the given numbers and keep every "
            "better/worse judgement exactly as stated in the facts. Plain text only: no markdown symbols "
            "(#, *, ---); put each section title on its own line.", ["analytics"]), facts, agent="shlok.report")
    except Exception as e:  # report still useful without the LLM
        bus.log(brief.client_id, "shlok.report", f"LLM unavailable ({e.__class__.__name__}); plain report")
        text = facts
    bus.say(brief.client_id, "shlok.report", "human", "INFORM", {"report": text, "totals": t})
    return text


def weekly_html(state: dict, days: int = 7, branding: dict | None = None, file_base: str = "/files/") -> str:
    """A client-ready report (white-labelled, prints to PDF): KPIs for the last `days`, daily spend, the best ads
    with their pictures, what the agents changed, the agents' own summary. Every number computed here."""
    import html
    from collections import defaultdict
    from datetime import timedelta

    from core.schemas import CreativePackage

    from . import live
    b = branding or {"agency_name": "Rune Studio", "color": "#D4F34A", "logo": None}
    brief = ClientBrief.model_validate(state["brief"])
    plan = BudgetPlan.model_validate(state["budget_plan"])
    cid, cur = state["client_id"], plan.currency
    rows = live.load_insights(cid)
    end = max((r.ts for r in rows), default=None)
    rows = [r for r in rows if end and r.ts > end - timedelta(days=days)]
    stats, t = live.ad_stats(rows), {}
    t = totals(stats)
    daily: dict[str, float] = defaultdict(float)
    for r in rows:
        daily[r.ts.date().isoformat()] += r.spend
    pkg = CreativePackage.model_validate(state["package"])
    adsets = {s.ref: s.name for s in pkg.blueprint.adsets()}
    creatives = {c.creative_id: c for c in pkg.creatives}
    creatives |= {ref: creatives[ref.split("@")[0]] for ref in stats if ref.split("@")[0] in creatives}  # reused ads
    best = sorted(stats.items(), key=lambda x: (x[1]["cpa"] is None, x[1]["cpa"] or 0, -x[1]["ctr"]))[:5]
    from core.schemas import now
    acts = [a for a in live.load_actions(cid) if a.ts >= now() - timedelta(days=days)]  # real-time events
    summary = next((m["payload"].get("report", "") for m in reversed(bus.history(cid)) if m["sender"] == "shlok.report"), "")
    e = lambda x: html.escape(str(x))
    money = lambda v: "—" if v is None else f"{cur} {v:,.0f}"
    peak = max(daily.values(), default=0) or 1
    bars = "".join(f'<div class="bar"><i style="height:{v / peak * 100:.0f}%"></i><span>{d[8:]}</span></div>'
                   for d, v in sorted(daily.items()))
    ads = "".join(
        f'<tr><td>{f"<img src={chr(34)}{file_base}{e(creatives[ref].thumbnail_path or creatives[ref].path)}{chr(34)}>" if ref in creatives else ""}'
        f'{e(creatives[ref].ad_copy.headline if ref in creatives else ref)}<em> · {e(creatives[ref].ratio) if ref in creatives else ""}'
        f'{" · retargeting" if "@" in ref else ""}</em></td><td>{money(s["spend"])}</td>'
        f'<td>{s["ctr"]:.2%}</td><td>{s["conversions"]}</td><td>{money(s["cpa"])}</td></tr>' for ref, s in best)
    who = lambda ref: creatives[ref].ad_copy.headline if ref in creatives else adsets.get(ref, "the campaign")
    changes = "".join(f"<li><b>{e(ACTION_WORDS.get(a.kind, a.kind))}</b> {e(who(a.ref))}: {e(a.reason)}"
                      f" <em>({e(a.status)})</em></li>" for a in acts[-12:]) or "<li>No changes needed this period.</li>"
    logo = f'<img class="logo" src="{e(b["logo"] if str(b.get("logo") or "").startswith("http") else file_base + str(b.get("logo")))}">' if b.get("logo") else ""
    target = plan.target_cpa or brief.kpi_targets.target_cpa
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{e(brief.business_name)} · {days}-day report</title><style>
:root {{ --accent: {e(b.get("color") or "#D4F34A")}; }}
* {{ box-sizing: border-box; }} body {{ font-family: Inter, Segoe UI, Arial, sans-serif; margin: 0; color: #15171c; background: #f4f5f7; }}
.page {{ max-width: 920px; margin: 0 auto; padding: 32px 20px 60px; }}
header {{ display: flex; align-items: center; gap: 14px; border-bottom: 4px solid var(--accent); padding-bottom: 16px; }}
.logo {{ height: 40px; }} h1 {{ font-size: 26px; margin: 0; }} .sub {{ color: #5b6270; font-size: 14px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 22px 0; }}
.kpi {{ background: #fff; border-radius: 14px; padding: 14px 16px; }} .kpi b {{ display: block; font-size: 24px; margin-top: 4px; }}
.kpi span {{ color: #5b6270; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
section {{ background: #fff; border-radius: 14px; padding: 18px 20px; margin: 14px 0; }} h2 {{ font-size: 15px; margin: 0 0 12px; }}
.bars {{ display: flex; align-items: flex-end; gap: 6px; height: 140px; }} .bar {{ flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; }}
.bar i {{ display: block; width: 100%; background: #15171c; border-radius: 6px 6px 0 0; }} .bar span {{ font-size: 11px; color: #5b6270; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }} td, th {{ padding: 8px 6px; border-bottom: 1px solid #eceef2; text-align: left; }}
td img {{ height: 44px; width: 36px; object-fit: cover; border-radius: 6px; vertical-align: middle; margin-right: 10px; }}
li {{ margin: 6px 0; font-size: 13px; }} em {{ color: #5b6270; font-style: normal; }} pre {{ white-space: pre-wrap; font: inherit; font-size: 14px; line-height: 1.55; }}
footer {{ color: #8a909c; font-size: 12px; margin-top: 24px; }} @media print {{ body {{ background: #fff; }} section, .kpi {{ border: 1px solid #eceef2; }} }}
</style></head><body><div class="page">
<header>{logo}<div><h1>{e(brief.business_name)}: {days}-day performance</h1>
<div class="sub">Prepared by {e(b.get("agency_name") or "")} · {e(end.date() if end else "no data yet")}</div></div></header>
<div class="kpis">
<div class="kpi"><span>Spend</span><b>{money(t["spend"])}</b></div>
<div class="kpi"><span>Results</span><b>{t["conversions"]}</b></div>
<div class="kpi"><span>Cost per result</span><b>{money(t["cpa"])}</b>{f'<em>target {money(target)}</em>' if target else ""}</div>
<div class="kpi"><span>ROAS</span><b>{t["roas"]:.2f}</b></div>
<div class="kpi"><span>CTR</span><b>{t["ctr"]:.2%}</b></div></div>
{f'<section><h2>Summary</h2><pre>{e(summary)}</pre></section>' if summary else ""}
<section><h2>Daily spend</h2><div class="bars">{bars or "No spend yet."}</div></section>
<section><h2>Best ads</h2><table><tr><th>Ad</th><th>Spend</th><th>CTR</th><th>Results</th><th>Cost/result</th></tr>{ads}</table></section>
<section><h2>What we changed</h2><ul>{changes}</ul></section>
<footer>{e(b.get("agency_name") or "")} · budget {money(plan.total)} · {plan.days} days</footer>
</div></body></html>"""
