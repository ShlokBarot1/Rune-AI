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


def test_intake_quick_parse_typed_answers():
    from malav.intake import quick_parse
    d = {"n_images": 2, "n_videos": 2, "ratios": ["4:5", "9:16"]}
    assert quick_parse("deliverables", "1 image 1 video 1 ad set", d) == {"n_images": 1, "n_videos": 1, "ratios": ["4:5", "9:16"]}
    assert quick_parse("deliverables", "3 images only, 1:1", d) == {"n_images": 3, "n_videos": 0, "ratios": ["1:1"]}
    assert quick_parse("kpis", "CPA of 1,500 INR", {}) == {"target_cpa": 1500.0}
    assert quick_parse("kpis", "we want roas 3.5", {}) == {"target_roas": 3.5}
    assert quick_parse("deliverables", "whatever you think", d) == {}
    b = {"budget_total": 30000.0, "currency": "INR"}
    assert quick_parse("budget", "10000 dollars", b) == {"budget_total": 10000.0, "currency": "USD"}
    assert quick_parse("budget", "$2.5k", b) == {"budget_total": 2500.0, "currency": "USD"}
    assert quick_parse("budget", "1.5 lakh", b) == {"budget_total": 150000.0, "currency": "INR"}
    assert quick_parse("budget", "around fifty thousand", b) == {}
    from datetime import date
    assert quick_parse("dates", "2026-10-28 to 2026-11-26", {}) == {"start": date(2026, 10, 28), "end": date(2026, 11, 26)}


def test_scene_prompt_keeps_only_what_the_image_model_can_use():
    from malav.creatives import scene_note
    from malav.media import colour_name
    audit = ("Fix these audit findings:\n"
             "- [major] cr_a_4x5: Creative QA did not pass: Failed checks: has_text_layer\n"
             "- [major] cr_b_4x5: background too dark\n"
             "- [minor] cr_a_4x5: background too busy behind the headline")
    assert scene_note(audit, "cr_a_4x5") == "background too busy behind the headline"
    assert scene_note("Failed checks: has_text_layer, exact_size", "cr_a_4x5") == ""
    assert scene_note("regenerate cr_a_4x5 with brighter light", "cr_a_4x5") == "regenerate with brighter light"
    assert [colour_name(c) for c in ("#112337", "#204CE5", "#3B2A20", "#E8D9C4", "#FFFFFF", "red")] == \
           ["navy blue", "vivid blue", "brown", "beige", "white", "red"]


def test_wearables_go_on_a_model():
    from types import SimpleNamespace as N
    from malav.references import wearable
    assert wearable(N(industry="Streetwear", products=[N(name="Da Bird oversized tee")]))
    assert not wearable(N(industry="Higher education", products=[N(name="B.Tech admissions")]))
    assert not wearable(N(industry="Coffee", products=[N(name="Cold brew concentrate")]))


def test_stop_unwinds_the_running_stage():
    from typing import TypedDict
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from core import bus

    class S(TypedDict, total=False):
        n: int

    def slow(state):
        bus.STOPPED.add("c_stop")  # the human presses Stop while this stage runs
        try:
            bus.log("c_stop", "malav.qa", "still working")
        except Exception:  # agents' broad fallbacks must not swallow a stop
            raise AssertionError("Stopped was swallowed")
        return {"n": 1}

    g = StateGraph(S)
    g.add_node("first", lambda s: {"n": 0})
    g.add_node("slow", slow)
    g.add_edge(START, "first"); g.add_edge("first", "slow"); g.add_edge("slow", END)
    app, cfg = g.compile(checkpointer=InMemorySaver()), {"configurable": {"thread_id": "c_stop"}}
    try:
        app.invoke({}, cfg)
        raise AssertionError("run was not stopped")
    except bus.Stopped:
        pass
    finally:
        bus.STOPPED.discard("c_stop")
    assert app.get_state(cfg).next == ("slow",)  # checkpoint stays at the stopped stage's start


def test_strategy_builds_exactly_the_requested_deliverables():
    from malav.strategy import _ads, max_adsets_for
    brief = ClientBrief.model_validate(load("brief"))
    count = lambda ads: sorted(a.format for v in ads.values() for a in v)
    for ni, nv, sets in ((1, 1, 1), (2, 2, 2), (3, 0, 1), (4, 2, 3), (0, 1, 1)):
        b = brief.model_copy(update={"deliverables": brief.deliverables.model_copy(update={"n_images": ni, "n_videos": nv})})
        n = max_adsets_for(b)
        assert n == sets, (ni, nv, n)
        ads = _ads([f"as_{i}" for i in range(min(n, 3))], b)
        assert count(ads) == sorted(["image"] * ni + ["video"] * nv), (ni, nv, count(ads))
