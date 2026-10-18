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
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import BaseModel

from core import bus, llm, skills
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
body, religion, etc.), no guarantees or unrealistic claims. Never use the brand's don't-words."""

CRITIC_SYSTEM = """You are a harsh ad critic. Score each hook 1-10 on scroll-stop power, clarity,
persona fit, brand fit and policy safety (a policy risk caps the score at 3). One short note each."""

CONCEPT_SYSTEM = """For each hook write Meta ad copy and a visual concept.
headline <= 40 chars; primary_text starts with the hook and its first 125 chars carry it; description
<= 30 chars; cta from the enum. scene = a photographic background/setting for the product (lighting,
props, mood, brand colours) with NO text, NO logos and NO product in it (the real product photo is
composited later). on_screen_text <= 8 words. voiceover = 25-35 words, hook first. Same policy rules."""


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


def _brief_bits(brief: ClientBrief, persona: Optional[Persona], adset: AdSetPlan, note: str = "") -> str:
    p = brief.products[0]
    s = (f"Product: {p.name}, price {p.price} {brief.budget.currency}, USPs {p.usp}\n"
         f"Brand tone: {brief.brand.tone}; do-words {brief.brand.do_words}; don't-words {brief.brand.dont_words}\n"
         f"Ad set: {adset.name}; angle: {adset.angle}; notes: {adset.rationale}\n")
    if persona:
        s += (f"Persona {persona.name}: {persona.description}; pains {persona.pains}; desires {persona.desires}; "
              f"objections {persona.objections}; their words {persona.phrases}\n")
    return s + (f"\nHuman instruction (apply it): {note}" if note else "")


def _fallback_hooks(brief: ClientBrief, adset: AdSetPlan, n: int) -> list[_Gen]:
    p = brief.products[0]
    lines = [f"{adset.angle[:1].upper()}{adset.angle[1:]}. Meet {p.name}."]
    lines += [f"{u}." for u in p.usp]
    if p.price:
        lines.append(f"{p.name} for {brief.budget.currency} {p.price:,.0f}.")
    lines += [f"{p.name}: {u.lower()}." for u in p.usp]
    while len(lines) < n:
        lines.append(f"Try {p.name} today ({len(lines) + 1}).")
    return [_Gen(text=t, framework="fallback") for t in lines]


def hooks_for_adset(cid: str, brief: ClientBrief, persona: Optional[Persona], adset: AdSetPlan, n_keep: int,
                    context_doc: str = "", note: str = "") -> list[HookPlus]:
    """Generate -> policy filter -> critic -> keep n_keep -> copy + concept. Ids are assigned by the caller."""
    bits = _brief_bits(brief, persona, adset, note)
    try:
        gen = llm.structured("creative", skills.with_skills(GEN_SYSTEM, ["ad-creative", "marketing-psychology"], context_doc),
                             bits, _Hooks).hooks
        bus.log(cid, AGENT, f"{adset.ref}: generated {len(gen)} hooks (skills: ad-creative, marketing-psychology)")
    except Exception as e:
        bus.log(cid, AGENT, f"{adset.ref}: hook model unavailable ({e.__class__.__name__}); using brief-based hooks")
        gen = []
    safe = []
    for g in gen:
        issues = policy_issues(g.text, brief)
        if issues:
            bus.log(cid, AGENT, f"Policy pre-check dropped: “{g.text}” ({'; '.join(issues)})")
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
        bus.log(cid, AGENT, f"{adset.ref}: critic unavailable ({e.__class__.__name__}); keeping generation order")
    order = sorted(range(len(safe)), key=lambda i: -(scores[i].score if i in scores else 5 - i * 0.01))
    kept = [(safe[i], scores[i].score if i in scores else None) for i in order[:n_keep]]
    bus.log(cid, AGENT, f"{adset.ref}: critic kept {len(kept)}/{len(safe)}: " +
            " | ".join(f"{s if s is not None else '-'} “{g.text}”" for g, s in kept))

    concepts: dict[int, _Concept] = {}
    try:
        listing = "\n".join(f"{i}. {g.text}" for i, (g, _) in enumerate(kept))
        res = llm.structured("creative", skills.with_skills(CONCEPT_SYSTEM, ["copywriting"]),
                             f"{bits}\nGoal: {brief.goal} (default CTA {GOAL_CTA[brief.goal]})\nHooks:\n{listing}", _Concepts)
        concepts = {c.index: c for c in res.concepts}
    except Exception as e:
        bus.log(cid, AGENT, f"{adset.ref}: copywriter unavailable ({e.__class__.__name__}); using template copy")

    out = []
    p = brief.products[0]
    for i, (g, score) in enumerate(kept):
        c = concepts.get(i) or _Concept(index=i, headline=p.name, primary_text=f"{g.text} {' · '.join(p.usp[:2])}",
                                        description=(f"{brief.budget.currency} {p.price:,.0f}" if p.price else ""),
                                        on_screen_text=g.text)
        copy = fit_copy(AdCopy(primary_text=c.primary_text, headline=c.headline, description=c.description,
                               cta=c.cta or GOAL_CTA[brief.goal]), g.text, brief)
        if policy_issues(f"{copy.primary_text} {copy.headline} {copy.description}", brief):
            copy = fit_copy(AdCopy(primary_text=g.text, headline=p.name, cta=GOAL_CTA[brief.goal]), g.text, brief)
        out.append(HookPlus(id="", adset_ref=adset.ref, text=g.text, framework=g.framework, critic_score=score,
                            ad_copy=copy, scene=c.scene or f"{adset.angle}, lifestyle setting, soft natural light",
                            on_screen_text=_cut(c.on_screen_text or g.text, 60), voiceover=c.voiceover or g.text))
    return out


def hooks_step(state: RuneState) -> dict:
    cid = state["client_id"]
    if demo_mode(state):
        bus.log(cid, AGENT, "Demo run: using the fixture hooks")
        return {"hooks": fixture("hooks")}

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

    bus.log(cid, AGENT, f"Writing hooks for {len(bp.adsets())} ad sets" + (f"; applying: {note}" if note else ""))
    with ThreadPoolExecutor(max_workers=2) as ex:
        hooks = [h for hs in ex.map(one, bp.adsets()) for h in hs]
    return {"hooks": hooks}
