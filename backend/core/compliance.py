"""Restricted ad categories: what Meta forbids, needs written authorisation for, or limits (age, wording).

check(text) finds categories from the brief's words; every stage asks rules_for(brief) what applies:
  intake     records brief.restricted and sets Meta's special ad category when one applies
  targeting  raises the minimum age
  hooks      adds the category's copy rules to the copywriter's prompt
  auditor    blocks go-live for forbidden categories, and for ones needing authorisation the client hasn't confirmed

ponytail: keyword matching on the brief. It catches the common cases; Meta's own review is still the final word,
and the disapproval handling in shlok.live catches what this misses.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel


class Rule(BaseModel):
    category: str
    action: Literal["blocked", "authorization", "restricted"]
    min_age: int = 18
    special: Optional[str] = None   # Meta special ad category this forces
    copy_rules: str = ""


_RULES: list[tuple[str, Rule]] = [
    (r"tobacco|cigarettes?|cigars?|vapes?|vaping|e-?cigarettes?|hookah|nicotine",
     Rule(category="tobacco & vaping", action="blocked", copy_rules="Meta does not allow ads selling tobacco or vapes.")),
    (r"firearms?|guns?|ammunition|ammo|explosives",
     Rule(category="weapons", action="blocked", copy_rules="Meta does not allow ads selling weapons or ammunition.")),
    (r"escort|adult (content|entertainment)|porn",
     Rule(category="adult content", action="blocked", copy_rules="Meta does not allow adult content.")),
    (r"alcohol|liquor|whisk(e)?y|vodka|\bbeer\b|\bwine\b|\brum\b|\bgin\b|brewery|distillery",
     Rule(category="alcohol", action="restricted", min_age=25,
          copy_rules="Alcohol: never target or depict minors, never imply better social, sexual or athletic success, "
                     "no excessive drinking; follow local law (several countries ban alcohol ads).")),
    (r"casino|betting|\bbet\b|gambling|lottery|poker|fantasy (sports|league)|real[- ]money gam\w*",
     Rule(category="gambling & real-money games", action="authorization", min_age=18,
          copy_rules="Gambling: needs Meta's written permission and a licence in each target country; no appeal to "
                     "minors, no promise of winning.")),
    (r"crypto|bitcoin|blockchain token|\bnft\b|forex|trading signals",
     Rule(category="crypto & trading", action="authorization", min_age=18,
          copy_rules="Crypto / trading: needs Meta's written permission; no guaranteed returns.")),
    (r"\bloans?\b|credit cards?|\bemi\b|mortgage|insurance|mutual funds?|investment|\bdemat\b|stock broking",
     Rule(category="financial services", action="restricted", special="credit",
          copy_rules="Financial products: Meta's special ad category (no age, gender or postcode targeting); show "
                     "rates and terms honestly, no guaranteed returns.")),
    (r"prescription|online pharmacy|pharmac(y|ies)|telemedicine|rx\b",
     Rule(category="pharmacy & prescription drugs", action="authorization", min_age=18,
          copy_rules="Prescription drugs / online pharmacy: needs certification (e.g. LegitScript) and Meta's permission.")),
    (r"weight[- ]loss|slimming|fat burn\w*|diet pills?|cosmetic surgery|liposuction|botox",
     Rule(category="weight loss & cosmetic procedures", action="restricted", min_age=18,
          copy_rules="Weight loss / cosmetic: 18+ only; no before-and-after images, no body shaming, no claims of a "
                     "number of kilos in a time span.")),
    (r"supplements?|nutraceutical|ayurvedic medicine|herbal remed\w*",
     Rule(category="health supplements", action="restricted", min_age=18,
          copy_rules="Supplements: no disease-cure or treatment claims, no unsafe-substance promotion.")),
    (r"dating|matchmaking|matrimon\w*",
     Rule(category="dating", action="authorization", min_age=18,
          copy_rules="Dating: needs Meta's written permission; no sexual content.")),
    (r"election|political party|candidate|campaign for (mla|mp|mayor)|referendum",
     Rule(category="politics & social issues", action="authorization", special="social_issues",
          copy_rules="Political / social issue ads: Meta authorisation and a 'Paid for by' disclaimer.")),
    (r"hiring|we'?re hiring|job openings?|recruitment|careers? at",
     Rule(category="employment", action="restricted", special="employment",
          copy_rules="Job ads: Meta's employment category (no age, gender or postcode targeting).")),
    (r"real estate|apartments? for (sale|rent)|flats? for (sale|rent)|property for (sale|rent)|housing",
     Rule(category="housing", action="restricted", special="housing",
          copy_rules="Housing ads: Meta's housing category (no age, gender or postcode targeting).")),
]


def check(text: str) -> list[Rule]:
    low = text.lower()
    return [rule for pattern, rule in _RULES if re.search(rf"\b(?:{pattern})\b", low)]


def brief_text(brief) -> str:
    return " ".join([brief.industry, brief.business_name, brief.audience_notes, *brief.constraints,
                     *(p.name for p in brief.products), *(u for p in brief.products for u in p.usp)])


def rules_for(brief) -> list[Rule]:
    """The rules that apply: the categories intake recorded (brief.restricted), else found now from the brief."""
    found = check(brief_text(brief))
    if brief.restricted:
        found = [r for r in found if r.category in brief.restricted] + [
            r for _, r in _RULES if r.category in brief.restricted and r.category not in {x.category for x in found}]
    return found


def min_age(brief) -> int:
    return max([18, *(r.min_age for r in rules_for(brief))])


def copy_rules(brief) -> str:
    return " ".join(r.copy_rules for r in rules_for(brief))
