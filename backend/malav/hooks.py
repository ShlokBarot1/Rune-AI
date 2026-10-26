"""M4 Hook & Concept Agent + Critic.

Per ad set: generate 8-12 hooks ("creative"), drop policy risks, a critic ("reason", a different model
family) scores them 1-10, keep the best (at least one per planned ad, 3-5 normally), then write copy
and a visual concept for each kept hook. Kept hooks fill the blueprint's hook slots (ad.hook_id) in
score order; extras are kept for the feedback loop.

state["hooks"] items are `Hook` dicts plus Malav-only keys (ad_copy, scene, on_screen_text, voiceover)
that the creatives step reads. `Hook.model_validate` ignores the extras, so the contract still holds.
"""
from __future__ import annotations

import re
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import BaseModel

from core import bus, calendar, compliance, langs, llm, skills
from core.schemas import CTA, AdCopy, AdSetPlan, CampaignBlueprint, ClientBrief, Hook, Persona, ResearchReport
from core.state import RuneState
from malav.common import demo_mode, fixture

AGENT = "malav.hooks"
GOAL_CTA = {"sales": "SHOP_NOW", "leads": "SIGN_UP", "traffic": "LEARN_MORE", "awareness": "LEARN_MORE",
            "engagement": "LEARN_MORE", "app_installs": "DOWNLOAD"}
FRAMEWORKS = ["problem-agitate-solve", "price anchor", "social proof", "curiosity gap", "bold claim",
              "UGC/testimonial", "listicle", "us vs them", "offer-led", "founder story", "speed/convenience"]

# Meta: never assert or imply a personal attribute of the viewer; no guaranteed/unrealistic outcomes
_ATTRS = (r"overweight|fat|obese|depress\w*|anxious|anxiety|diabet\w*|bald|broke|debts?|single|divorced|pregnant|"
          r"gay|lesbian|christian|muslim|hindu|disab\w*|acne|wrinkl\w*|infertil\w*|addict\w*")
_YOU_ATTR = re.compile(rf"\b(you|you're|you are|your)\b[^.?!]{{0,40}}\b({_ATTRS})\b", re.I)
_CLAIMS = re.compile(r"\b(guarantee\w*|100% (results|effective)|risk[- ]free|cure\w*|miracle|instant results|lose \d+ ?kg)\b", re.I)


class HookPlus(Hook):
    ad_copy: Optional[AdCopy] = None
    scene: str = ""              # background scene for the image model: no text, logos or product
    on_screen_text: str = ""     # short overlay, rendered by the HTML template
    voiceover: str = ""          # 6-15 s script, hook in the first 3 s


class _Gen(BaseModel):
    text: str
    framework: str = ""


class _Hooks(BaseModel):
    hooks: list[_Gen]


class _Score(BaseModel):
    index: int
    score: float          # 1-10
    note: str = ""


class _Scores(BaseModel):
    scores: list[_Score]


class _Concept(BaseModel):
    index: int
    headline: str
    primary_text: str
    description: str = ""
    cta: Optional[CTA] = None
    scene: str = ""
    on_screen_text: str = ""
    voiceover: str = ""


class _Concepts(BaseModel):
    concepts: list[_Concept]


GEN_SYSTEM = f"""You write scroll-stopping Meta ad hooks (the first line / first 3 seconds). Write 10
hooks for ONE ad set, mixing frameworks: {', '.join(FRAMEWORKS)}. Max 12 words each. Use the persona's
verbatim language. Policy: never assert or imply the viewer's personal attributes (health, finances,
body, religion, etc.), no guarantees or unrealistic claims. Never use the brand's don't-words.
Facts only: every number (price, grams, pack count, %), comparison and superlative must come from the product facts
given. Never invent a price, a quantity, a competitor comparison or "#1 / only / scam" style claims."""

CRITIC_SYSTEM = """You are a harsh ad critic. Score each hook 1-10 on scroll-stop power, clarity,
persona fit, brand fit and policy safety (a policy risk caps the score at 3). One short note each."""

CONCEPT_SYSTEM = """For each hook write Meta ad copy and a visual concept.
headline <= 40 chars; primary_text starts with the hook and its first 125 chars carry it; description
<= 30 chars; cta from the enum. scene = a photographic background/setting for the product (lighting,
props, mood, brand colours) with NO text, NO logos and NO product in it (the real product photo is
composited later). on_screen_text <= 8 words. voiceover = 25-35 words, hook first. Same policy rules, and facts
only: no number, price, quantity or comparison that isn't in the product facts."""


# numbers that make a claim: prices, quantities, percentages, durations, counts
_NUMBER = re.compile(r"[₹$€£]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:%|x\b|[a-z]+\b)?", re.I)
# claims nobody may make without proof on the brief
_UNPROVEN = re.compile(r"\b(no (other )?competitor|only (brand|one|company)|#\s?1|number one|best in (india|the world|class)|"
                       r"cheapest|lowest price|scam|clinically (proven|tested)|doctor[- ]recommended|dermatologist[- ]"
                       r"(tested|recommended)|lab[- ]tested|certified|award[- ]winning|most trusted|world'?s first)\b", re.I)


def facts_of(brief: ClientBrief, context_doc: str = "") -> str:
    """Everything the copy may cite: the brief (products, prices, facts, offer, notes) and the site-derived context."""
    return f"{brief.model_dump_json()} {context_doc}".lower()


def ungrounded(text: str, facts: str) -> list[str]:
    """Numbers and strong claims in `text` the facts don't contain: made-up prices, pack counts, protein grams,
    comparisons. A number counts as grounded when its digits appear in the facts (1 is always fine)."""
    issues = []
    digits_in_facts = set(re.findall(r"\d+(?:\.\d+)?", facts.replace(",", "")))
    for m in _NUMBER.finditer(text):
        num = re.search(r"\d+(?:\.\d+)?", m.group(0).replace(",", "")).group(0)
        if num not in ("0", "1") and num not in digits_in_facts:
            issues.append(f"“{m.group(0).strip()}” isn't in the product facts")
    for m in _UNPROVEN.finditer(text):
        if m.group(0).lower() not in facts:
            issues.append(f"“{m.group(0)}” is a claim the brief doesn't back")
    return issues


def policy_issues(text: str, brief: ClientBrief) -> list[str]:
    out = []
    if _YOU_ATTR.search(text):
        out.append("implies a personal attribute of the viewer")
    if _CLAIMS.search(text):
        out.append("guarantee or unrealistic claim")
    out += [f"uses don't-word '{w}'" for w in brief.brand.dont_words if re.search(rf"\b{re.escape(w)}\b", text, re.I)]
    return out


def _cut(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n + 1].rsplit(" ", 1)[0].rstrip(",;:-—") or text[:n]


def _cut_sentence(text: str, n: int) -> str:
    """Trim to <= n chars at a sentence end; a word boundary + "…" if no sentence ends late enough."""
    if len(text) <= n:
        return text
    head = text[:n]
    end = max(head.rfind("."), head.rfind("!"), head.rfind("?"))
    return head[:end + 1] if end >= n * 0.4 else _cut(text, n - 1) + "…"


def _opens_with(primary: str, hook: str) -> bool:
    """Does the copy already carry the hook up front (allowing light rewording)?"""
    hw = set(re.findall(r"\w+", hook.lower()))
    return not hw or len(hw & set(re.findall(r"\w+", primary[:160].lower()))) / len(hw) >= 0.6


def fit_copy(c: AdCopy, hook: str, brief: ClientBrief) -> AdCopy:
    """Meta limits enforced in code: headline <= 40, description <= 30, hook in the first 125 chars,
    primary text <= 250 (the auditor flags > 300), never cut mid-sentence."""
    primary = " ".join(c.primary_text.split())
    if not _opens_with(primary[:125], hook):
        primary = f"{hook} {primary}".strip()
    return AdCopy(primary_text=_cut_sentence(primary, 250), headline=_cut(c.headline, 40), description=_cut(c.description, 30),
                  cta=c.cta or GOAL_CTA[brief.goal])


REWRITE_SYSTEM = """You fix Meta ad copy that an auditor flagged. Rewrite only what the issues require, keep the
hook, offer and voice. headline <= 40 chars, description <= 30, primary_text <= 250 with the hook in the first 125.
Policy: never assert or imply the viewer's personal attributes (health, emotions, finances, body, religion...),
no guarantees or unrealistic claims, no unsourced claims about competitors. Never use the brand's don't-words."""


def rewrite_copy(cid: str, brief: ClientBrief, copy: AdCopy, issues: list[str]) -> AdCopy:
    """Audit copy findings -> corrected copy (text only: the image stays). The old copy if no model answers."""
    try:
        new = llm.structured("creative", REWRITE_SYSTEM,
                             f"Brand tone: {brief.brand.tone}; don't-words {brief.brand.dont_words}\n"
                             f"Current copy: {copy.model_dump_json()}\nIssues:\n" + "\n".join(f"- {i}" for i in issues),
                             AdCopy)
        new = fit_copy(new, new.primary_text[:60], brief)
        if left := policy_issues(f"{new.primary_text} {new.headline} {new.description}", brief):
            raise ValueError("; ".join(left))
        return new
    except Exception as e:
        bus.log(cid, AGENT, f"Couldn't rewrite the copy ({e.__class__.__name__}); it stays for you to edit")
        return copy


FOCUS = {
    "brand": "Campaign focus: the brand / category as a whole, not one product. Don't sell a single SKU or price; "
             "scenes show the lifestyle or moment, with no product in them.\n",
    "photos": "Visual: the client's own photos (their place, team, work or app) are the ad image, so write copy that "
              "fits a real photo of the business; the scene field is not used.\n",
    "range": "Campaign focus: the RANGE ({names}). Sell the category and the choice: why this range (its key benefit, "
             "variety, price, trust, the brand's proof points), with products named as examples. Ads show several products "
             "side by side plus benefit tags; carousels add a card per product, so the copy introduces the range, not "
             "one SKU.\n",
    "product": "",
}


def situation(brief: ClientBrief, adset: AdSetPlan) -> str:
    """What else the copy has to carry: the offer, the moments in the flight, the neighbourhood, the language,
    the audience, and the category's ad rules."""
    out = ""
    if (o := brief.offer) and not (o.ends and o.ends < date.today()):  # an offer that's over is never written again
        bits = [o.headline] + ([f"code {o.code}"] if o.code else []) + (
            [f"ends {o.ends:%d %b}"] if o.ends else []) + ([o.terms] if o.terms else [])
        out += (f"Offer (lead with it, quote it exactly, never invent another): {'; '.join(bits)}. Put the code and "
                f"the end date in the primary text.\n")
    if moments := calendar.events_in(brief.budget.start, brief.budget.end, brief.geo):
        out += f"Moments in this flight (use one where it fits naturally): {calendar.describe(moments[:4])}\n"
    if brief.locations:
        out += (f"Local business at {', '.join(l.name + (f' ({l.city})' if l.city else '') for l in brief.locations[:5])}: "
                f"speak to people nearby (the area, 'near you', how close it is), not the whole country.\n")
    language = adset.language or (brief.languages[0] if brief.languages else "en")
    if language != "en":
        out += (f"Write every text (hooks, headline, primary text, on-screen text, voiceover) in {langs.name(language)}, "
                f"in its own script (e.g. Devanagari for Hindi), the way people there actually speak it; everyday English "
                f"words people use are fine. Only if the client asked for Hinglish / romanised text, write it in Latin "
                f"script.\n")
    if adset.audience == "retargeting":
        out += "Audience: people who already visited or engaged: remind, reassure, give a reason to come back now.\n"
    if rules := compliance.copy_rules(brief):
        out += f"Category rules (Meta policy): {rules}\n"
    return out


def _brief_bits(brief: ClientBrief, persona: Optional[Persona], adset: AdSetPlan, note: str = "") -> str:
    p = brief.product(adset.product)
    others = [q.name for q in brief.products if q.name != p.name]
    s = (f"Business: {brief.business_name} ({brief.industry})\n"
         f"{'Featured product' if adset.product else 'Product / offer'}: {p.name}, price {p.price} {brief.budget.currency}, "
         f"USPs {p.usp}" + (f"; the brand also sells {', '.join(others[:5])}" if others else "") + "\n"
         f"Brand tone: {brief.brand.tone}; do-words {brief.brand.do_words}; don't-words {brief.brand.dont_words}\n"
         f"Ad set: {adset.name}; angle: {adset.angle}; notes: {adset.rationale}\n")
    s += FOCUS[brief.creative_focus].replace("{names}", ", ".join(q.name for q in brief.products[:8]))
    s += situation(brief, adset)
    if persona:
        s += (f"Persona {persona.name}: {persona.description}; pains {persona.pains}; desires {persona.desires}; "
              f"objections {persona.objections}; their words {persona.phrases}\n")
    return s + (f"\nHuman instruction (apply it): {note}" if note else "")


def _fallback_hooks(brief: ClientBrief, adset: AdSetPlan, n: int) -> list[_Gen]:
    p = brief.product(adset.product)
    from malav.lineup import _PACK
    facts = [u for u in p.usp if not _PACK.search(u)]  # sizes and counts aren't hooks
    lines = [f"{adset.angle[:1].upper()}{adset.angle[1:]}. Meet {p.name}."]
    lines += [f"{u}." for u in facts]
    if p.price and brief.creative_focus == "product":
        lines.append(f"{p.name} for {brief.budget.currency} {p.price:,.0f}.")
    lines += [f"{p.name}: {u.lower()}." for u in facts]
    while len(lines) < n:
        lines.append(f"Try {p.name} today ({len(lines) + 1}).")
    return [_Gen(text=t, framework="fallback") for t in lines]


def hooks_for_adset(cid: str, brief: ClientBrief, persona: Optional[Persona], adset: AdSetPlan, n_keep: int,
                    context_doc: str = "", note: str = "") -> list[HookPlus]:
    """Generate -> policy + facts filter -> critic -> keep n_keep -> copy + concept (facts-checked, rewritten when it
    invents something). Ids are assigned by the caller."""
    bits = _brief_bits(brief, persona, adset, note)
    facts = facts_of(brief, context_doc)
    try:
        gen = llm.structured("creative", skills.with_skills(GEN_SYSTEM, ["ad-creative", "marketing-psychology"], context_doc),
                             bits, _Hooks).hooks
        bus.log(cid, AGENT, f"{adset.name}: wrote {len(gen)} opening lines")
    except Exception as e:
        bus.log(cid, AGENT, f"{adset.name}: hook model unavailable ({e.__class__.__name__}); using brief-based hooks")
        gen = []
    safe = []
    for g in gen:
        issues = policy_issues(g.text, brief) + ungrounded(g.text, facts)
        if issues:
            bus.log(cid, AGENT, f"Dropped: “{g.text}” ({'; '.join(issues)})")
        else:
            safe.append(g)
    if len(safe) < n_keep:
        safe += _fallback_hooks(brief, adset, n_keep)[: n_keep - len(safe)]

    scores: dict[int, _Score] = {}
    try:
        listing = "\n".join(f"{i}. [{g.framework}] {g.text}" for i, g in enumerate(safe))
        res = llm.structured("reason", skills.with_skills(CRITIC_SYSTEM, ["copy-editing"]),
                             f"{bits}\nHooks:\n{listing}", _Scores)
        scores = {s.index: s for s in res.scores if 0 <= s.index < len(safe)}
    except Exception as e:
        bus.log(cid, AGENT, f"{adset.name}: critic unavailable ({e.__class__.__name__}); keeping generation order")
    order = sorted(range(len(safe)), key=lambda i: -(scores[i].score if i in scores else 5 - i * 0.01))
    kept = [(safe[i], scores[i].score if i in scores else None) for i in order[:n_keep]]
    bus.log(cid, AGENT, f"{adset.name}: kept the best {len(kept)} of {len(safe)}: " +
            " | ".join(f"“{g.text}”" + (f" ({s:.0f}/10)" if s is not None else "") for g, s in kept))

    concepts: dict[int, _Concept] = {}
    try:
        listing = "\n".join(f"{i}. {g.text}" for i, (g, _) in enumerate(kept))
        res = llm.structured("creative", skills.with_skills(CONCEPT_SYSTEM, ["copywriting"]),
                             f"{bits}\nGoal: {brief.goal} (default CTA {GOAL_CTA[brief.goal]})\nHooks:\n{listing}", _Concepts)
        concepts = {c.index: c for c in res.concepts}
    except Exception as e:
        bus.log(cid, AGENT, f"{adset.ref}: copywriter unavailable ({e.__class__.__name__}); using template copy")

    out = []
    p = brief.product(adset.product)
    one = brief.creative_focus == "product"  # a range / brand / photo ad never quotes one product's price
    for i, (g, score) in enumerate(kept):
        c = concepts.get(i) or _Concept(index=i, headline=p.name, primary_text=f"{g.text} {' · '.join(p.usp[:2])}",
                                        description=(f"{brief.budget.currency} {p.price:,.0f}" if p.price and one else ""),
                                        on_screen_text=g.text)
        copy = fit_copy(AdCopy(primary_text=c.primary_text, headline=c.headline, description=c.description,
                               cta=c.cta or GOAL_CTA[brief.goal]), g.text, brief)
        if policy_issues(f"{copy.primary_text} {copy.headline} {copy.description}", brief):
            copy = fit_copy(AdCopy(primary_text=g.text, headline=p.name, cta=GOAL_CTA[brief.goal]), g.text, brief)
        if made_up := ungrounded(f"{copy.primary_text} {copy.headline} {copy.description}", facts):
            bus.log(cid, AGENT, f"Copy invented facts ({'; '.join(made_up[:3])}); rewriting it from the facts")
            fixed = rewrite_copy(cid, brief, copy, made_up + ["Use only numbers and claims from the product facts."])
            copy = fixed if not ungrounded(f"{fixed.primary_text} {fixed.headline} {fixed.description}", facts) else \
                fit_copy(AdCopy(primary_text=g.text, headline=p.name[:40], cta=GOAL_CTA[brief.goal]), g.text, brief)
        if ungrounded(c.on_screen_text or "", facts):  # the words drawn on the image follow the same rule
            c.on_screen_text = g.text if not ungrounded(g.text, facts) else copy.headline
        out.append(HookPlus(id="", adset_ref=adset.ref, text=g.text, framework=g.framework, critic_score=score,
                            ad_copy=copy, scene=c.scene or f"{adset.angle}, lifestyle setting, soft natural light",
                            on_screen_text=_cut(c.on_screen_text or g.text, 60), voiceover=c.voiceover or g.text))
    return out


def hooks_step(state: RuneState) -> dict:
    cid = state["client_id"]
    if demo_mode(state):
        bus.log(cid, AGENT, "Demo run: using the fixture hooks")
        return {"hooks": fixture("hooks", cid)}

    brief = ClientBrief.model_validate(state["brief"])
    research = ResearchReport.model_validate(state["research"])
    bp = CampaignBlueprint.model_validate(state["blueprint"])
    personas = {p.ref: p for p in research.personas}
    note = (state.get("notes") or {}).get("hooks", "")

    def one(adset: AdSetPlan) -> list[dict]:
        slots = [a.hook_id for a in adset.ads]
        kept = hooks_for_adset(cid, brief, personas.get(adset.persona_ref), adset, max(len(slots), min(5, len(slots) + 2)),
                               state.get("context_doc", ""), note)
        short = adset.ref[3:]
        ids = slots + [f"h_{short}_x{i}" for i in range(1, len(kept) - len(slots) + 1)]
        return [h.model_copy(update={"id": i}).model_dump(mode="json") for i, h in zip(ids, kept)]

    own_ads = [a for a in bp.adsets() if a.ads]  # retargeting / catalog ad sets run other ads or Meta's catalog
    bus.log(cid, AGENT, f"Writing hooks for {len(own_ads)} ad sets" + (f"; applying: {note}" if note else ""))
    with ThreadPoolExecutor(max_workers=2) as ex:
        hooks = [h for hs in ex.map(one, own_ads) for h in hs]
    from malav import director  # the looks to choose from, shown with the hooks at their gate
    looks = director.propose(cid, brief, research.summary, [h["text"] for h in hooks])
    return {"hooks": hooks, "directions": [d.model_dump() for d in looks]}
