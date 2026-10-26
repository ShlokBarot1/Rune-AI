"""The state object that flows through the whole pipeline.

Ownership (who may WRITE each key):
  malav:   brief, research, blueprint, hooks, package, context_doc
  shlok:   budget_plan, targeting, launch, live
  auditor: audit, audit_round
  core:    notes (human change requests per step)
Everyone may read everything. Values are plain dicts (model_dump(mode="json"))
so checkpoints serialise cleanly; validate with Model.model_validate(state[key]).
"""
from __future__ import annotations

from typing import Callable, NamedTuple, TypedDict


class RuneState(TypedDict, total=False):
    client_id: str
    intake: dict          # raw first input from the user (website URL, notes, uploads)
    context_doc: str      # product-marketing context markdown shared with every skill
    brief: dict           # ClientBrief
    research: dict        # ResearchReport
    blueprint: dict       # CampaignBlueprint
    hooks: list           # list[Hook]
    package: dict         # CreativePackage
    budget_plan: dict     # BudgetPlan
    targeting: list       # list[TargetingSpec]
    launch: dict          # LaunchResult
    audit: dict           # AuditReport
    audit_round: int
    notes: dict           # step name -> human change request the step must apply on rerun
    directions: list      # malav: the creative director's proposed looks (malav.director.Direction)
    direction: dict       # malav: the look the human picked; every creative follows it
    live: bool


class Step(NamedTuple):
    """One pipeline stage. `run(state) -> dict` returns only the keys it changed.

    gate=True puts a human approval gate after the step; the card shows `outputs`.
    """
    name: str
    run: Callable[[RuneState], dict]
    outputs: tuple[str, ...]
    gate: bool = True
