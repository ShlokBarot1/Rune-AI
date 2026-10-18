"""Marketing skills (coreyhaines31/marketingskills submodule) as agent knowledge.

Each agent picks its skills (mapping in PLAN.md §3.1) and gets them appended to
its system prompt. The load is logged to the bus so Agent Theater shows it.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "skills" / "marketingskills" / "skills"
MAX_CHARS_PER_SKILL = 12_000  # ponytail: hard truncation; split by section if free-model context gets tight


@lru_cache(maxsize=None)
def load(name: str) -> str:
    text = (ROOT / name / "SKILL.md").read_text(encoding="utf-8")
    if text.startswith("---"):  # drop frontmatter
        text = text.split("---", 2)[2]
    return text.strip()[:MAX_CHARS_PER_SKILL]


def available() -> list[str]:
    return sorted(p.parent.name for p in ROOT.glob("*/SKILL.md"))


def with_skills(system: str, names: list[str], context_doc: str = "") -> str:
    """System prompt + client context doc (what the skills call product-marketing context) + skill playbooks."""
    parts = [system]
    if context_doc:
        parts.append("# Product marketing context (the client)\n" + context_doc)
    parts += [f"# Skill: {n}\n{load(n)}" for n in names]
    return "\n\n".join(parts)
