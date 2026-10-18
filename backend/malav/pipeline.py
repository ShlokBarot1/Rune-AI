"""MALAV'S PIPELINE — placeholder stubs that return fixture data so the whole system runs today.

Malav: replace each stub with the real agent (in its own module if you like), keeping:
  * STEPS: list[Step] — add/rename steps freely; the LAST one must set state["package"]
  * every step writes only the keys it owns (see core/state.py) as JSON dicts
  * apply_fixes(state, prompts) -> dict
  * handle_feedback(state, feedback) -> CreativePackage
tests/test_pipeline.py must keep passing.
"""
from __future__ import annotations

from core import bus
from core.schemas import CreativeFeedback, CreativePackage, FixPrompt
from core.state import RuneState, Step
from malav import intake, research, strategy
from malav.common import fixture as _fixture


def hooks(state: RuneState) -> dict:
    bus.log(state["client_id"], "malav.hooks", "STUB: loaded fixture hooks")
    return {"hooks": _fixture("hooks")}


def creatives(state: RuneState) -> dict:
    bus.log(state["client_id"], "malav.packager", "STUB: loaded fixture creative package")
    return {"package": _fixture("package")}


STEPS = [
    Step("intake", intake.graph, ("brief", "context_doc")),  # a subgraph: one node per question
    Step("research", research.research_step, ("research", "context_doc")),
    Step("strategy", strategy.strategy_step, ("blueprint",)),
    Step("hooks", hooks, ("hooks",)),
    Step("creatives", creatives, ("package",)),
]


def apply_fixes(state: RuneState, prompts: list[FixPrompt]) -> dict:
    """Auditor/human fix prompts owned by Malav. Return changed keys (e.g. a new "package")."""
    for p in prompts:
        bus.log(state["client_id"], "malav", f"STUB: would fix {p.object_refs}")
    return {}


def handle_feedback(state: RuneState, feedback: CreativeFeedback) -> CreativePackage:
    """Shlok's optimizer asks for new creatives. Return a package with ONLY the new creatives
    (new creative_ids, parent_id = the winner they iterate on, same adset_ref)."""
    bus.log(state["client_id"], "malav.hooks", f"STUB: got feedback for {feedback.adset_ref}")
    pkg = CreativePackage.model_validate(state["package"])
    return CreativePackage(client_id=pkg.client_id, blueprint=pkg.blueprint, creatives=[])
