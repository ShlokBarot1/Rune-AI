"""Creative director: one coherent look for the whole campaign, chosen by the human before any image is made.

propose() writes 3 distinct directions (hooks step, shown with the hooks); creatives_step asks the human to pick
one; Studio then follows it for every ad: the layouts, the photography of every scene, the reference search.
Without a model, a fixed set of proven directions per focus stands in.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from core import bus, llm
from core.schemas import ClientBrief

AGENT = "malav.director"

# layouts per focus, best first (templates/*.html); offer-badge only joins when an offer runs (Studio.template)
LAYOUTS = {
    "product": ["packshot", "brand-block", "product-hero", "type-bold"],
    "range": ["range-lineup", "brand-block", "type-bold"],
    "photos": ["brand-block", "product-hero", "type-bold"],
    "brand": ["type-bold", "brand-block", "product-hero"],
}
LAYOUT_NOTES = {
    "packshot": "the product big and centred on the brand colour, benefit tags under it (e-commerce hero)",
    "brand-block": "photo above, a solid brand-colour panel with the headline below (classic FMCG)",
    "product-hero": "full-bleed photo, headline over it, product in the lower part",
    "type-bold": "huge headline on the brand colour, the photo as a framed card (awareness)",
    "range-lineup": "several products side by side with benefit tags (a range)",
    "ugc-caption": "a quote in speech-bubble style over a casual photo (social proof)",
}


class Direction(BaseModel):
    name: str                 # "Clean packshot hero"
    pitch: str                # one line for the human: what it looks like and why it fits
    layouts: list[str]        # template ids in order, cycled over the ads
    photography: str          # one style for every scene: light, lens, palette, mood
    scenes: str               # what the scenes show (product in use, where, with what)
    reference_query: str = "" # image search for references in this look
    avoid: str = ""


class _Directions(BaseModel):
    directions: list[Direction]


SYSTEM = """You are the creative director of a top ad agency. Propose 3 clearly different visual directions for a
Meta campaign, each a complete look every ad will follow: layouts (only from the allowed list, best first),
photography (one consistent style: light, lens, colour grade, mood; the brand's colours as accents), scenes (what
the photos show: the brand's real product in real use, in places this audience lives; concrete, never abstract
metaphors or props that need explaining), a short reference image search query, and what to avoid. Think about what
wins in this category on Instagram and Facebook: real product clearly visible, thumb-stopping contrast, one idea per
ad, recognisably the brand within a second. name: 2-4 words; pitch: one sentence a client understands."""


def fallback(brief: ClientBrief) -> list[Direction]:
    lay = LAYOUTS[brief.creative_focus]
    cat = brief.industry or "product"
    return [
        Direction(name="Clean hero", pitch="Your product big and clear on your brand colour; benefits up front.",
                  layouts=lay, photography="bright studio light, soft shadows, crisp detail, brand colour accents",
                  scenes=f"the product on a clean surface with one or two natural props from its use ({cat})",
                  reference_query=f"{cat} product photography studio minimal"),
        Direction(name="Real-life moments", pitch="Your product in the everyday moments your buyers know.",
                  layouts=[x for x in ("brand-block", "product-hero") if x in lay + ["product-hero"]] or lay,
                  photography="natural window light, warm grade, shallow depth of field, candid",
                  scenes=f"the product being used at home / on the go by the people who buy {cat}",
                  reference_query=f"{cat} lifestyle photography candid"),
        Direction(name="Bold statement", pitch="Big confident type in your colours; built to stop the scroll.",
                  layouts=["type-bold", *[x for x in lay if x != "type-bold"]],
                  photography="high-contrast editorial, saturated brand colours, graphic composition",
                  scenes="one striking close-up of the product or its key ingredient",
                  reference_query=f"{cat} bold graphic advertising"),
    ]


def propose(cid: str, brief: ClientBrief, summary: str = "", hooks: Optional[list[str]] = None) -> list[Direction]:
    allowed = LAYOUTS[brief.creative_focus]
    try:
        got = llm.structured("reason", SYSTEM, (
            f"Brand: {brief.business_name} ({brief.industry}). Tone: {brief.brand.tone}. Colours {brief.brand.colors}.\n"
            f"What the ads show: {brief.creative_focus}; products: {[p.name for p in brief.products[:8]]}\n"
            f"Audience / research: {summary[:1500]}\nHooks: {(hooks or [])[:8]}\n"
            f"Allowed layouts: " + "; ".join(f"{k}: {LAYOUT_NOTES[k]}" for k in allowed)), _Directions).directions
        out = []
        for d in got[:3]:
            d.layouts = [x for x in d.layouts if x in allowed] or allowed
            out.append(d)
        if len(out) < 2:
            raise ValueError("too few directions")
    except Exception as e:
        bus.log(cid, AGENT, f"Creative director unavailable ({e.__class__.__name__}); proven directions instead")
        out = fallback(brief)
    bus.log(cid, AGENT, "Directions: " + " | ".join(f"{d.name}: {d.pitch}" for d in out))
    return out


def option(d: Direction) -> str:
    return f"{d.name}: {d.pitch}"


def pick(directions: list[Direction], answer: str) -> Direction:
    """The direction an answer names (the option text, its name, or a number); the first otherwise."""
    a = answer.strip().lower()
    for i, d in enumerate(directions):
        if a == option(d).lower() or a.startswith(d.name.lower()) or a in (str(i + 1), d.name.lower()):
            return d
    return next((d for d in directions if d.name.lower() in a), directions[0])
