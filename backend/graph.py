"""Top-level pipeline: malav.STEPS -> shlok.STEPS -> audit <-> fix -> human go-live -> activate.

Neither owner edits this file to add a stage: append a Step to your own STEPS list.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import malav.pipeline as malav
import shlok.pipeline as shlok
from auditor.agent import audit
from core import bus
from core.approvals import auto, make_gate
from core.schemas import AuditReport, FixPrompt, GateDecision
from core.state import RuneState

MAX_AUDIT_ROUNDS = 3


def audit_node(state: RuneState) -> Command:
    rnd = state.get("audit_round", 0) + 1
    report = audit(state)
    update = {"audit": report.model_dump(mode="json"), "audit_round": rnd}
    fixable = any(p.owner in ("malav", "shlok") for p in report.fix_prompts)  # else only the human can fix it
    if report.decision == "FAIL" and rnd < MAX_AUDIT_ROUNDS and fixable:
        return Command(goto="fix", update=update)
    return Command(goto="go_live", update=update)


def fix_node(state: RuneState) -> dict:
    """Send each fix prompt to its owner. Malav first (may change creatives), then Shlok (re-syncs Meta)."""
    report = AuditReport.model_validate(state["audit"])
    for p in report.fix_prompts:
        bus.say(state["client_id"], "auditor", p.owner, "FIX_PROMPT", p.model_dump())
    pick = lambda owner: [p for p in report.fix_prompts if p.owner in (owner, "human")]
    update = dict(malav.apply_fixes(state, pick("malav")))
    update |= shlok.apply_fixes({**state, **update}, pick("shlok"))
    return update


def go_live_node(state: RuneState) -> Command:
    if auto():
        return Command(goto="activate")
    d = GateDecision.model_validate(interrupt({"type": "go_live", "audit": state.get("audit")}))
    bus.say(state["client_id"], "human", "go_live", "RESPONSE", d.model_dump())
    if d.action == "approve":
        return Command(goto="activate")
    if d.action == "reject":
        return Command(goto=END)
    report = AuditReport.model_validate(state["audit"])
    report.fix_prompts.append(FixPrompt(owner="human", object_refs=[], prompt=d.comment))
    return Command(goto="fix", update={"audit": report.model_dump(mode="json"), "audit_round": 0})


def build(checkpointer=None):
    steps = [*malav.STEPS, *shlok.STEPS]
    after = [s.name for s in steps[1:]] + ["audit"]
    g = StateGraph(RuneState)
    g.add_edge(START, steps[0].name)
    for step, nxt in zip(steps, after):
        g.add_node(step.name, step.run)
        if step.gate:
            g.add_node(f"{step.name}__gate", make_gate(step, nxt))
            g.add_edge(step.name, f"{step.name}__gate")
        else:
            g.add_edge(step.name, nxt)
    g.add_node("audit", audit_node)
    g.add_node("fix", fix_node)
    g.add_node("go_live", go_live_node)
    g.add_node("activate", shlok.activate)
    g.add_edge("fix", "audit")
    g.add_edge("activate", END)
    return g.compile(checkpointer=checkpointer)
