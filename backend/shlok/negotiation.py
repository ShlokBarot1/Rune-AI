"""Budget negotiation between Malav's strategy agent and Shlok's budget agent, over the agent bus.

Round loop (max MAX_ROUNDS):
  strategy -> PROPOSAL (ad sets + evidence + argument)
  budget   -> REPLY: accept, or counter with which ad sets to keep / merge and why
  strategy -> revised PROPOSAL (concede, swap one ad set, or argue with evidence) ... until agreement

Shlok's budget agent reasons with an LLM, grounded by budget_feasibility() numbers. Guardrail: whatever
the LLM says, the agreement never keeps more ad sets than the money supports (code enforces it).

Malav's side plugs in through `revise(proposal, reply, history) -> Proposal`. Without one, a default
LLM reviser speaks for "malav.strategy" so the negotiation still runs.
"""
from __future__ import annotations

import json
from typing import Callable, Literal, Optional

from pydantic import BaseModel

from core import bus, llm, skills
from core.schemas import ClientBrief

from .budget import TESTING_RESERVE, budget_feasibility

MAX_ROUNDS = 3
BUDGET = "shlok.budget"


class AdSetPitch(BaseModel):
    key: str                 # stable id for this idea, e.g. "p_wfh|café price"
    name: str
    evidence: str = ""       # why it should exist (research evidence)


class Proposal(BaseModel):
    adsets: list[AdSetPitch]  # best first
    argument: str = ""


class BudgetReply(BaseModel):
    decision: Literal["accept", "counter"]
    keep: list[str]           # pitch keys to keep, best first
    merge_into: dict[str, str] = {}  # dropped key -> kept key whose ads should carry its angle as hooks
    message: str              # what the budget agent says to the strategy agent


class Agreement(BaseModel):
    keep: list[str]
    merge_into: dict[str, str]
    rounds: int
    max_adsets: int
    summary: str


BUDGET_SYSTEM = """You are the budget agent of a Meta ads agency negotiating campaign structure with the
strategy agent. Each ad set needs enough daily budget to exit Meta's learning phase; the facts give the
exact maximum number of ad sets the budget supports. Decide which ad sets to keep (never more than the
maximum), preferring the strongest evidence and diversity of personas. For each dropped ad set, name a kept
ad set whose ads can test its angle as hooks. If the proposal already fits, accept it. Be concise and
specific in the message; cite the numbers."""

STRATEGY_SYSTEM = """You are the strategy agent of a Meta ads agency negotiating with the budget agent.
You may accept its counter, or propose a different set of ad sets within its stated maximum, swapping one
in only if your evidence for it is clearly stronger. Never propose more ad sets than the budget agent's
maximum. Keep the argument to 1-2 sentences."""


def _facts(brief: ClientBrief, n: int) -> dict:
    f = budget_feasibility(brief, n_adsets=n, asked_by=BUDGET)  # the agent's calculator tool, logged on the bus
    return {"max_adsets": f.max_adsets, "min_daily_per_adset": f.min_daily_per_adset,
            "total_daily": f.total_daily, "spendable_daily_after_reserve": round(f.total_daily * (1 - TESTING_RESERVE)),
            "currency": brief.budget.currency, "note": f.suggestion}


def budget_reply(brief: ClientBrief, proposal: Proposal, history: list[dict]) -> tuple[BudgetReply, int]:
    facts = _facts(brief, len(proposal.adsets))
    keys = [p.key for p in proposal.adsets]
    try:
        reply = llm.structured(
            "reason", skills.with_skills(BUDGET_SYSTEM, ["ads"]),
            f"Facts: {json.dumps(facts)}\nProposal: {proposal.model_dump_json()}\n"
            f"Earlier rounds: {json.dumps(history)[-3000:]}", BudgetReply, agent=BUDGET)
    except Exception as e:  # no model: plain rule, still a valid counter
        bus.log(brief.client_id, BUDGET, f"reasoning unavailable ({e.__class__.__name__}); using the rule")
        keep = keys[:max(1, facts["max_adsets"])]
        reply = BudgetReply(decision="accept" if len(keep) == len(keys) else "counter", keep=keep,
                            merge_into={k: keep[0] for k in keys if k not in keep}, message=facts["note"])

    # guardrails: only proposed keys, never more than the budget supports
    cap = max(1, facts["max_adsets"])
    keep = [k for k in reply.keep if k in keys][:cap] or keys[:cap]
    merge = {d: (reply.merge_into.get(d) if reply.merge_into.get(d) in keep else keep[0]) for d in keys if d not in keep}
    decision = "accept" if keep == keys else "counter"
    return reply.model_copy(update={"keep": keep, "merge_into": merge, "decision": decision}), facts["max_adsets"]


def default_revise(proposal: Proposal, reply: BudgetReply, max_adsets: int, history: list[dict]) -> Proposal:
    """Speaks for malav.strategy when Malav's code doesn't pass its own reviser."""
    try:
        return llm.structured(
            "reason", STRATEGY_SYSTEM,
            f"Budget agent maximum: {max_adsets}\nIts reply: {reply.model_dump_json()}\n"
            f"Your previous proposal: {proposal.model_dump_json()}\nEarlier rounds: {json.dumps(history)[-3000:]}",
            Proposal, agent="shlok.negotiation")
    except Exception:
        kept = [p for p in proposal.adsets if p.key in reply.keep]
        return Proposal(adsets=kept, argument="Accepted the counter-proposal.")


def negotiate(brief: ClientBrief, proposal: Proposal,
              revise: Optional[Callable[[Proposal, BudgetReply, int, list[dict]], Proposal]] = None,
              asked_by: str = "malav.strategy") -> Agreement:
    cid, history = brief.client_id, []
    all_pitches = {p.key: p for p in proposal.adsets}
    for rnd in range(1, MAX_ROUNDS + 1):
        bus.say(cid, asked_by, BUDGET, "REQUEST", {
            "text": f"Round {rnd}: proposing {len(proposal.adsets)} ad set(s): "
                    + ", ".join(p.name for p in proposal.adsets) + (f". {proposal.argument}" if proposal.argument else ""),
            "proposal": proposal.model_dump()})
        reply, max_adsets = budget_reply(brief, proposal, history)
        bus.say(cid, BUDGET, asked_by, "RESPONSE", {"text": reply.message, "decision": reply.decision,
                                                     "keep": reply.keep, "max_adsets": max_adsets})
        history.append({"round": rnd, "proposal": [p.key for p in proposal.adsets], "reply": reply.model_dump()})
        if reply.decision == "accept" or rnd == MAX_ROUNDS:
            break
        proposal = (revise or default_revise)(proposal, reply, max_adsets, history)
        for p in proposal.adsets:  # the reviser may bring back an earlier idea by key
            all_pitches.setdefault(p.key, p)

    names = [all_pitches[k].name for k in reply.keep if k in all_pitches]
    summary = (f"Agreed after {rnd} round(s): {len(reply.keep)} ad set(s) — {', '.join(names)}."
               + (f" Dropped angles become hooks: {reply.merge_into}." if reply.merge_into else ""))
    bus.say(cid, BUDGET, asked_by, "INFORM", {"text": summary})
    return Agreement(keep=reply.keep, merge_into=reply.merge_into, rounds=rnd, max_adsets=max_adsets, summary=summary)
