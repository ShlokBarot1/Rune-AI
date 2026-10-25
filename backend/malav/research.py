"""M2 Research: three agents in parallel (audience, competitors, trends) -> synthesizer -> ResearchReport.

The branches run in a ThreadPoolExecutor inside one step. They never call ask_human (an interrupt
can't happen inside a worker thread); each branch catches its own failures, so one flaky free model
or a 429 never sinks the others. The step also adds a research section to the context doc.
"""
from __future__ import annotations

import json
import os
import re
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from pydantic import BaseModel

from core import bus, calendar, langs, llm, skills
from core.schemas import ClientBrief, Competitor, Persona, ResearchReport
from core.state import RuneState
from malav import web
from malav.common import demo_mode, fixture

EU = ["DE", "FR", "IT", "ES", "NL", "IE", "SE", "PL"]


class Ctx(BaseModel):
    cid: str
    brief: ClientBrief
    context_doc: str = ""
    note: str = ""             # human change request from the research gate, applied by every branch

    def ask(self, text: str) -> str:
        return f"{text}\n\nHuman instruction for this rerun (apply it): {self.note}" if self.note else text


class _Audience(BaseModel):
    product_insights: list[str] = []
    personas: list[Persona] = []


class _CompetitorNames(BaseModel):
    competitors: list[Competitor] = []


class _CompetitorAnalysis(BaseModel):
    competitors: list[Competitor] = []
    white_space: list[str] = []          # angles nobody in the category is using


class _TrendPlan(BaseModel):
    keywords: list[str] = []             # 2-3 short Google search terms for the category
    seasonal_hooks: list[str] = []       # festivals, sales events, seasons inside the flight


class _Synthesis(BaseModel):
    summary: str
    recommended_angles: list[str] = []


AUDIENCE_SYSTEM = """You are the audience researcher of an ad agency. From the search results, build
2-5 buyer personas for Meta ads. Each persona: a short ref like "p_wfh", pains, desires, objections,
verbatim customer phrases copied from the sources (never invented), interests as plain words a Meta
interest search would find, and evidence = the source URLs you used. Also list product insights:
benefits, proof points, price positioning, objections."""

COMPETITOR_LIST_SYSTEM = """From the search results, list up to 4 direct competitors of the client
(same category and geo). name and homepage url only. Never list the client itself."""

COMPETITOR_SYSTEM = """You are the competitor analyst of an ad agency. For each competitor, read their
website text and ads and record ad_notes (offers, formats, claims) and angles (the hook/positioning
types they lean on, e.g. "price anchor", "health", "convenience", "social proof"). Then list white
space: angles the client could own that no competitor uses."""

TREND_SYSTEM = """You are the trend scout of an ad agency. Give 2-3 short Google search keywords for
the client's category, and seasonal hooks (festivals, sale events, seasons, paydays) in the client's
countries that fall inside or just before the campaign dates. One line each, with the date."""

SYNTH_SYSTEM = """You merge an ad agency's research into an executive summary (3-4 sentences) and
3-6 recommended ad angles ranked by evidence strength. Each angle: "<angle> — <persona> — <why, citing
the evidence>"."""


def _sources(results: list[dict], limit: int = 7000) -> str:
    out, size = [], 0
    for i, r in enumerate(results, 1):
        line = f"[{i}] {r['url']}\n{r.get('title', '')}: {r.get('content', '')[:500]}"
        if size + len(line) > limit:
            break
        out.append(line)
        size += len(line)
    return "\n\n".join(out) or "(no search results available)"


def _search_all(ctx: Ctx, agent: str, queries: list[str], n: int = 5) -> list[dict]:
    seen: dict[str, dict] = {}
    for q in queries:
        try:
            for r in web.search(q, n):
                seen.setdefault(r["url"], r)
            bus.log(ctx.cid, agent, f"Searched: {q}")
        except Exception as e:
            bus.log(ctx.cid, agent, f"Search failed for '{q}' ({e.__class__.__name__})")
    return list(seen.values())


def _generic(b: ClientBrief) -> str:
    """'Brewbloom Cold Brew Concentrate (500ml)' -> 'cold brew concentrate': what customers actually search
    and review. A new D2C brand has no reviews under its own name, so the audience mines the category."""
    name = re.sub(r"\(.*?\)", "", b.products[0].name)
    for w in b.business_name.split():
        name = re.sub(rf"\b{re.escape(w)}\b", "", name, flags=re.I)
    name = " ".join(name.split()).lower()
    # "First Collection", "Summer Drop", "Starter Set" name a release, not a product: search the industry instead
    vague = re.fullmatch(r"(the |our |new |first |latest |summer |winter |spring |fall |starter |signature )*"
                         r"(collection|drop|set|bundle|kit|range|line|edition|release)s?( \d+)?", name)
    return b.industry.lower() if (not name or vague) else name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:20] or "persona"


def _fallback_persona(b: ClientBrief) -> Persona:
    return Persona(ref="p_core", name="Core buyer", description=b.audience_notes or f"Buyers of {b.products[0].name}",
                   interests=[b.industry], desires=[u for p in b.products for u in p.usp][:3])


# ---------- the three branches ----------

def audience(ctx: Ctx) -> dict:
    agent, b = "malav.research.audience", ctx.brief
    product, geo = _generic(b), " ".join(b.geo)
    bus.log(ctx.cid, agent, f"Mining reviews and Reddit for '{product}' (skills: customer-research)")
    # customers who speak other languages review and complain in them: search there too
    other = [langs.name(x) for x in b.languages if x and x != "en"][:2]
    results = _search_all(ctx, agent, [f"{product} reviews {geo}", f"{product} reddit",
                                       f"{b.business_name} reviews", f"{product} complaints problems",
                                       *(f"{product} review in {lang_name}" for lang_name in other)])
    extra = ""
    thread = next((r["url"] for r in results if "reddit.com" in r["url"]), None)
    if thread:
        try:
            extra = f"\n\nFull Reddit thread ({thread}):\n{web.read_page(thread, 3000)}"
            bus.log(ctx.cid, agent, f"Reading thread {thread}")
        except Exception:
            pass
    out = llm.structured("reason", skills.with_skills(AUDIENCE_SYSTEM, ["customer-research"], ctx.context_doc),
                         ctx.ask(f"Brief:\n{b.model_dump_json()}\n\nSources:\n{_sources(results)}{extra}"), _Audience)
    urls = {r["url"] for r in results}
    personas, refs = [], set()
    for p in out.personas[:5]:
        ref = p.ref if p.ref.startswith("p_") else f"p_{_slug(p.name)}"
        while ref in refs:
            ref += "_2"
        refs.add(ref)
        personas.append(p.model_copy(update={"ref": ref, "evidence": [u for u in p.evidence if u in urls]}))
    for p in personas:
        bus.log(ctx.cid, agent, f"Persona {p.name}: {len(p.phrases)} verbatim phrases, {len(p.evidence)} sources")
    return {"personas": personas, "product_insights": out.product_insights}


def competitors(ctx: Ctx) -> dict:
    agent, b = "malav.research.competitors", ctx.brief
    bus.log(ctx.cid, agent, "Finding competitors (skills: competitor-profiling)")
    results = _search_all(ctx, agent, [f"best {_generic(b)} brands {' '.join(b.geo)}",
                                       f"best {b.industry} brands {' '.join(b.geo)}",
                                       f"brands like {b.business_name} {b.industry}"])
    names = llm.structured("fast", COMPETITOR_LIST_SYSTEM,
                           ctx.ask(f"Client: {b.business_name} ({b.website})\n\nSources:\n{_sources(results, 5000)}"),
                           _CompetitorNames).competitors[:4]
    names = [c for c in names if c.name.lower() != b.business_name.lower()]
    bus.log(ctx.cid, agent, f"Competitors: {', '.join(c.name for c in names) or 'none found'}")

    # each competitor: Meta Ad Library, else their website, else a web search; one source failing isn't the end
    material = []
    use_ads = bool(os.getenv("META_ACCESS_TOKEN"))
    for c in names:
        if use_ads:
            try:
                ads = web.ad_library(c.name, EU)
                bodies = [" / ".join(a.get("ad_creative_bodies") or []) for a in ads][:6]
                material.append(f"## {c.name} — {len(ads)} ads in Meta Ad Library\n" + "\n".join(f"- {x[:300]}" for x in bodies))
                bus.log(ctx.cid, agent, f"Pulled {len(ads)} {c.name} ads from the Meta Ad Library")
                continue
            except Exception as e:
                use_ads = False  # a permission problem fails the same way for every brand
                bus.log(ctx.cid, agent, f"Meta Ad Library unavailable ({_why(e)}); reading websites and search results instead")
        try:
            if c.url:
                material.append(f"## {c.name} ({c.url}) website\n{web.read_page(c.url, 2500)}")
                bus.log(ctx.cid, agent, f"Read {c.name}'s website")
                continue
        except Exception:
            pass
        try:
            hits = web.search(f"{c.name} ad campaign {' '.join(b.geo)}", 4)
            material.append(f"## {c.name} (web search)\n" + "\n".join(f"- {h['title']}: {h['content'][:300]}" for h in hits))
            bus.log(ctx.cid, agent, f"Looked up {c.name}'s recent campaigns on the web")
        except Exception as e:
            bus.log(ctx.cid, agent, f"Couldn't find anything on {c.name} ({_why(e)})")
    if not names:
        return {"competitors": [], "white_space": []}
    out = llm.structured("reason", skills.with_skills(COMPETITOR_SYSTEM, ["competitor-profiling"], ctx.context_doc),
                         ctx.ask(f"Competitors: {[c.model_dump() for c in names]}\n\n" + "\n\n".join(material)[:9000]),
                         _CompetitorAnalysis)
    bus.log(ctx.cid, agent, f"White space: {'; '.join(out.white_space) or 'none'}")
    return {"competitors": out.competitors or names, "white_space": out.white_space}


def trends(ctx: Ctx) -> dict:
    agent, b = "malav.research.trends", ctx.brief
    bus.log(ctx.cid, agent, "Planning trend keywords and seasonal hooks (skills: social)")
    plan = llm.structured("fast", skills.with_skills(TREND_SYSTEM, ["social"]),
                          ctx.ask(f"Today: {date.today()}\nCategory: {b.industry}\nProduct: {b.products[0].name}\n"
                                  f"Countries: {b.geo}\nCampaign: {b.budget.start} to {b.budget.end}"), _TrendPlan)
    lines = [f"Seasonal: {h}" for h in plan.seasonal_hooks]
    try:
        lines += google_trends(plan.keywords[:3], b.geo[0])
        bus.log(ctx.cid, agent, f"Google Trends checked for {', '.join(plan.keywords[:3])}")
    except Exception as e:  # pytrends is unofficial and Google rate-limits it (429) often
        try:
            hits = [h for kw in plan.keywords[:2] for h in web.search(f"{kw} trends {date.today().year} {b.geo[0]}", 3)]
            lines += [f"Trend article: {h['title']}: {h['content'][:200]}" for h in hits]
            bus.log(ctx.cid, agent, f"Google Trends unavailable ({_why(e)}); read {len(hits)} trend articles instead")
        except Exception:
            bus.log(ctx.cid, agent, f"Google Trends unavailable ({_why(e)}); using seasonal hooks only")
    return {"trends": lines}


def _why(e: Exception) -> str:
    """A failure in words a client understands."""
    s = str(e)
    if "429" in s or "TooManyRequests" in e.__class__.__name__:
        return "Google is limiting requests right now"
    if "permission" in s.lower() or "App role required" in s or re.search(r"\b40[03]\b", s):
        return "our Meta app doesn't have access yet"
    if "timed out" in s.lower() or "Timeout" in e.__class__.__name__:
        return "the site took too long"
    return "the site blocked us"


def google_trends(keywords: list[str], geo: str) -> list[str]:
    from pytrends.request import TrendReq  # optional; rate-limits often, callers catch
    if not keywords:
        return []
    py = TrendReq(hl="en-US", tz=0)
    py.build_payload(keywords, timeframe="today 12-m", geo=geo)
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)  # pytrends vs pandas 2 noise
        iot = py.interest_over_time()
    for kw in keywords:
        if not iot.empty and kw in iot:
            out.append(f"Google Trends ({geo}): '{kw}' peaks in {iot[kw].idxmax():%B}")
    for kw, data in (py.related_queries() or {}).items():
        rising = data.get("rising") if data else None
        if rising is not None and not rising.empty:
            out.append(f"Rising searches for '{kw}': " + ", ".join(rising["query"].head(3)))
    return out


# ---------- step ----------

def _run(ctx: Ctx, fn, fallback: dict) -> dict:
    try:
        return fn(ctx)
    except Exception as e:
        bus.log(ctx.cid, f"malav.research.{fn.__name__}", f"Agent failed ({e.__class__.__name__}: {str(e)[:120]})")
        return fallback


def _synthesize(ctx: Ctx, parts: dict) -> _Synthesis:
    try:
        payload = json.dumps({k: v for k, v in parts.items()}, default=lambda m: m.model_dump())[:9000]
        return llm.structured("reason", skills.with_skills(SYNTH_SYSTEM, [], ctx.context_doc),
                              ctx.ask(f"Research:\n{payload}"), _Synthesis)
    except Exception as e:
        bus.log(ctx.cid, "malav.research", f"Synthesis fell back to a simple merge ({e.__class__.__name__})")
        angles = parts["white_space"] + [f"{d} — {p.name}" for p in parts["personas"] for d in p.desires[:1]]
        return _Synthesis(summary=f"{len(parts['personas'])} personas, {len(parts['competitors'])} competitors, "
                                  f"{len(parts['trends'])} trend signals for {ctx.brief.business_name}.",
                          recommended_angles=angles[:5])


def research_doc(doc: str, r: ResearchReport) -> str:
    """Add/replace the research block in the product-marketing context doc and bump its version."""
    rows = "\n".join(f"| {p.name} | {'; '.join(p.desires[:2])} | {'; '.join(p.pains[:2])} | {', '.join(p.interests[:4])} |"
                     for p in r.personas)
    block = (f"<!-- research:start -->\n## Research (M2)\n{r.summary}\n\n"
             f"| Persona | Wants | Pains | Interests |\n|---|---|---|---|\n{rows}\n\n"
             f"**Customer phrases:**\n" + "\n".join(f'- "{q}"' for p in r.personas for q in p.phrases[:3]) +
             "\n\n**Competitors:**\n" + "\n".join(f"- {c.name}: {', '.join(c.angles) or 'angles unknown'}" for c in r.competitors) +
             "\n\n**Trends and seasonal hooks:**\n" + "\n".join(f"- {t}" for t in r.trends) +
             "\n\n**Recommended angles:**\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(r.recommended_angles, 1)) +
             "\n<!-- research:end -->\n\n")
    if "<!-- research:start -->" in doc:
        doc = re.sub(r"<!-- research:start -->.*?<!-- research:end -->\n\n", lambda _: block, doc, flags=re.S)
    elif "## Learnings" in doc:
        doc = doc.replace("## Learnings", block + "## Learnings", 1)
    else:
        doc += "\n" + block
    m = re.search(r"\*\*Document version:\*\* v(\d+)", doc)
    if m:
        v = int(m.group(1)) + 1
        doc = doc.replace(m.group(0), f"**Document version:** v{v}", 1)
        doc = re.sub(r"\*\*Last updated:\*\* \S+", f"**Last updated:** {date.today()}", doc, count=1)
        doc = doc.replace("## Changelog\n", f"## Changelog\n- v{v} ({date.today()}) — Added research: personas, "
                                            f"competitors, trends, angles.\n", 1)
    return doc


def research_step(state: RuneState) -> dict:
    cid = state["client_id"]
    if demo_mode(state):
        bus.log(cid, "malav.research", "Demo run: using the fixture research")
        return {"research": fixture("research", cid)}

    ctx = Ctx(cid=cid, brief=ClientBrief.model_validate(state["brief"]), context_doc=state.get("context_doc", ""),
              note=(state.get("notes") or {}).get("research", ""))
    bus.log(cid, "malav.research", "Starting 3 research agents in parallel: audience, competitors, trends"
            + (f" (applying: {ctx.note})" if ctx.note else ""))
    with ThreadPoolExecutor(max_workers=3) as ex:
        fa = ex.submit(_run, ctx, audience, {"personas": [], "product_insights": []})
        fc = ex.submit(_run, ctx, competitors, {"competitors": [], "white_space": []})
        ft = ex.submit(_run, ctx, trends, {"trends": []})
        parts = {**fa.result(), **fc.result(), **ft.result()}
    if not parts["personas"]:
        parts["personas"] = [_fallback_persona(ctx.brief)]
        bus.log(cid, "malav.research", "No evidence-backed personas; using one core persona from the brief")

    b = ctx.brief  # shopping moments come from the calendar, not only from what the model remembers
    moments = calendar.events_in(b.budget.start, b.budget.end, b.geo)
    parts["trends"] = [f"Moment: {m.name}, {m.day:%d %b} (buying from {m.window[0]:%d %b})" for m in moments] + parts["trends"]
    from shlok.history import account_learnings
    past = account_learnings(cid)  # a brand that has advertised before: what already worked for it
    syn = _synthesize(ctx, {**parts, "account_history": past["text"] if past else []})
    report = ResearchReport(client_id=cid, summary=syn.summary, product_insights=parts["product_insights"],
                            personas=parts["personas"], competitors=parts["competitors"], trends=parts["trends"],
                            recommended_angles=([f"Proven in your account: {w[:120]}" for w in past["winners"]] if past else [])
                            + (syn.recommended_angles or parts["white_space"]),
                            benchmarks={"cpa": past["cpa"]} if past and past["cpa"] else {})
    bus.log(cid, "malav.research", f"Research ready: {len(report.personas)} personas, {len(report.competitors)} "
                                   f"competitors, {len(report.recommended_angles)} angles")
    out = {"research": report.model_dump(mode="json")}
    if state.get("context_doc"):
        out["context_doc"] = research_doc(state["context_doc"], report)
    return out
