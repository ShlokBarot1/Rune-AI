"""Human-in-the-loop: approval gates, agent questions, and the suggestion evaluator.

Gate flow after a Step:
  approve -> next step
  reject  -> rerun the step from scratch (reason in state.notes[step])
  changes -> Suggestion Evaluator checks the request against brief/policy/budget:
               valid   -> rerun the step with a concrete instruction in state.notes[step]
               invalid -> agent pushes back with reasons; human picks "apply anyway" or "keep current"
Set RUNE_AUTO_APPROVE=1 to skip every gate (tests, fixture runs).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from langgraph.types import Command, interrupt
from pydantic import BaseModel

from . import bus, llm
from .schemas import GateDecision
from .state import RuneState, Step


def auto() -> bool:
    return os.getenv("RUNE_AUTO_APPROVE") == "1"


def ask_human(client_id: str, agent: str, question: str, options: Optional[list[str]] = None) -> str:
    """An agent asks the user something it is unsure about.

    Call it at the START of a node (or in its own node): on resume LangGraph reruns the node
    from the top, so any work done before this call is repeated.
    """
    bus.say(client_id, agent, "human", "QUESTION", {"question": question, "options": options or []})
    if auto():
        return (options or [""])[0]
    answer = interrupt({"type": "question", "agent": agent, "question": question, "options": options or []})
    bus.say(client_id, "human", agent, "RESPONSE", {"answer": answer})
    return str(answer)


class Verdict(BaseModel):
    valid: bool
    reply: str          # what the agent says back to the human
    instruction: str    # concrete instruction the step applies when it reruns


EVALUATOR_SYSTEM = """You review a client's change request on an AI ad agency pipeline step.
Decide if applying it is correct: it must not break Meta advertising policy, must not exceed or
starve the budget (Meta minimum daily budgets, learning phase needs), and must stay consistent with
the client brief and research evidence. If it is correct, rewrite it as a precise instruction for the
agent. If it is not, explain why in 1-3 friendly sentences and propose a better alternative."""


def evaluate_suggestion(state: RuneState, step: str, comment: str, artifact: dict) -> Verdict:
    user = (f"Step: {step}\nClient brief: {json.dumps(state.get('brief', {}), default=str)[:6000]}\n"
            f"Current output: {json.dumps(artifact, default=str)[:8000]}\nHuman request: {comment}")
    try:
        return llm.structured("reason", EVALUATOR_SYSTEM, user, Verdict)
    except Exception as e:  # LLM down must never block the human
        bus.log(state["client_id"], "core.suggestions", f"evaluator unavailable ({e.__class__.__name__}); applying as-is")
        return Verdict(valid=True, reply="Applying your change.", instruction=comment)


def make_gate(step: Step, next_node: str):
    def gate(state: RuneState) -> Command:
        if auto():
            return Command(goto=next_node)
        cid = state["client_id"]
        artifact = {k: state.get(k) for k in step.outputs}
        decision = GateDecision.model_validate(interrupt({"type": "gate", "gate": step.name, "artifact": artifact}))
        bus.say(cid, "human", step.name, "RESPONSE", decision.model_dump())
        if decision.action == "approve":
            return Command(goto=next_node)

        notes = dict(state.get("notes") or {})
        if decision.action == "reject":
            notes[step.name] = f"The human rejected the previous output; start over. Reason: {decision.comment}"
            return Command(goto=step.name, update={"notes": notes})

        verdict = evaluate_suggestion(state, step.name, decision.comment, artifact)
        bus.say(cid, "core.suggestions", "human", "RESPONSE", verdict.model_dump())
        if not verdict.valid:
            again = GateDecision.model_validate(interrupt({
                "type": "pushback", "gate": step.name, "reply": verdict.reply,
                "hint": "approve = apply your change anyway, reject = keep current output",
            }))
            if again.action != "approve":
                return Command(goto=next_node)
            verdict.instruction = decision.comment
        notes[step.name] = verdict.instruction
        return Command(goto=step.name, update={"notes": notes})

    return gate
