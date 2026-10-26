"""MALAV'S PIPELINE: intake -> research -> strategy -> hooks -> creatives (each step gated by a human).

Contract (graph.py and Shlok's code use only these names):
  * STEPS: list[Step] — the LAST one sets state["package"]
  * every step writes only Malav's keys (brief, context_doc, research, blueprint, hooks, package) as JSON dicts
  * apply_fixes(state, prompts) -> dict
  * handle_feedback(state, feedback) -> CreativePackage   (only NEW creatives, parent_id = the winner)

Demo runs (RUNE_OFFLINE=1, or a run started without a website/notes, like CI) use the Brewbloom fixtures
at every step, so tests need no LLM, network or browser.
"""
from __future__ import annotations

from core.schemas import CreativeFeedback, CreativePackage, FixPrompt
from core.state import RuneState, Step
from malav import creatives, hooks, intake, research, strategy
from malav import feedback as loops

STEPS = [
    Step("intake", intake.graph, ("brief", "context_doc")),  # a subgraph: one node per question
    Step("research", research.research_step, ("research", "context_doc")),
    Step("strategy", strategy.strategy_step, ("blueprint",)),
    Step("hooks", hooks.hooks_step, ("hooks", "directions")),
    Step("creatives", creatives.creatives_step, ("package",)),
]


def apply_fixes(state: RuneState, prompts: list[FixPrompt]) -> dict:
    """Auditor/human fix prompts owned by Malav. Returns {"package": ...} when anything changed."""
    return loops.apply_fixes(state, prompts)


def handle_feedback(state: RuneState, feedback: CreativeFeedback) -> CreativePackage:
    """Shlok's optimizer asks for new creatives. Returns a package with ONLY the new creatives
    (new creative_ids, parent_id = the winner they iterate on, same adset_ref)."""
    return loops.handle_feedback(state, feedback)
