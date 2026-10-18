"""Agentic loops with a scripted model: guardrails hold no matter what the model decides."""
import json
from pathlib import Path

from core.schemas import BudgetPlan, ClientBrief, CreativePackage, LaunchResult

FIX = Path(__file__).resolve().parent.parent / "fixtures"
load = lambda n: json.loads((FIX / f"{n}.json").read_text(encoding="utf-8"))


def scripted(monkeypatch, replies):
    """Make llm.structured return the scripted replies in order (validated into the requested schema)."""
    it = iter(replies)
    monkeypatch.setattr("core.llm.structured", lambda alias, system, user, schema, **kw: schema.model_validate(next(it)))


def test_negotiation_caps_adsets(monkeypatch):
    from shlok.negotiation import AdSetPitch, Proposal, negotiate
    brief = ClientBrief.model_validate(load("brief"))  # budget supports 2 ad sets
    pitches = [AdSetPitch(key=k, name=k) for k in ("a", "b", "c")]
    scripted(monkeypatch, [
        {"decision": "accept", "keep": ["a", "b", "c"], "message": "sure"},   # budget LLM tries to accept 3
        {"adsets": [{"key": "a", "name": "a"}, {"key": "c", "name": "c"}], "argument": "c has stronger evidence"},
        {"decision": "accept", "keep": ["a", "c"], "message": "agreed"},
    ])
    deal = negotiate(brief, Proposal(adsets=pitches))
    assert deal.keep == ["a", "c"] and deal.rounds == 2 and deal.max_adsets == 2
    assert "b" not in deal.keep


def test_optimizer_agent_guardrails(monkeypatch):
    from graph import build
    from langgraph.checkpoint.memory import InMemorySaver
    from shlok import live, optimizer_agent, simulator

    monkeypatch.setenv("RUNE_AUTO_APPROVE", "1")
    state = build(InMemorySaver()).invoke({"client_id": "demo_brewbloom"}, {"configurable": {"thread_id": "o"}})
    brief, plan = ClientBrief.model_validate(state["brief"]), BudgetPlan.model_validate(state["budget_plan"])
    rows = simulator.simulate(brief, CreativePackage.model_validate(state["package"]), plan, days=7)
    live.save_insights("demo_brewbloom", rows)
    stats = live.ad_stats(rows)
    worst = max(stats, key=lambda r: stats[r]["cpa"] or 1e9)
    adset = stats[worst]["adset_ref"]
    scripted(monkeypatch, [
        {"thought": "look", "tool": "get_ads", "args": {"adset_ref": adset}},
        {"thought": "double it", "tool": "propose_budget_change", "args": {"adset_ref": adset, "new_daily": 99999}},
        {"thought": "pause worst", "tool": "pause_ad", "args": {"ad_ref": worst, "reason": "worst CPA"}},
        {"thought": "again", "tool": "pause_ad", "args": {"ad_ref": worst}},
        {"thought": "done", "tool": "finish", "args": {"summary": "paused the worst ad"}},
    ])
    update: dict = {}
    actions = optimizer_agent.run(state, brief, plan, LaunchResult.model_validate(state["launch"]), stats,
                                  live.monitor(brief, stats), rows, update)
    assert [a.kind for a in actions] == ["pause_ad"]          # the 99999 budget was rejected, double pause too
    assert actions[0].ref == worst and actions[0].status == "applied"
