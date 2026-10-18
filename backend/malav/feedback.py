"""The loops back into Malav's side: Shlok's CreativeFeedback (S7 -> M4) and Auditor/human fix prompts.

handle_feedback: n_new NEW creatives iterating on the winner (new creative_id, parent_id = winner,
same adset_ref, new ad refs appended to that ad set in the returned blueprint). Shlok merges them.
apply_fixes: copy limits are re-applied everywhere (cheap, deterministic); creatives the prompts name
are regenerated as a new version with the prompt as guidance; planned ads missing an asset are filled.
"""
from __future__ import annotations

from core import bus
from core.schemas import (RATIO_SIZES, AdPlan, ClientBrief, Creative, CreativeFeedback, CreativePackage, FixPrompt,
                          ResearchReport, new_id)
from core.state import RuneState
from malav.common import demo_mode
from malav.creatives import Job, creative_id, landing_url, pick_template, plan_jobs, produce, write_manifest
from malav.hooks import HookPlus, _fallback_hooks, fit_copy, hooks_for_adset

AGENT = "malav.hooks"


def _formats(fb: CreativeFeedback) -> list[tuple[str, str]]:
    out = []
    for f in fb.formats:
        parts = f.split()
        if len(parts) == 2 and parts[0] in ("image", "video") and parts[1] in RATIO_SIZES:
            out.append((parts[0], parts[1]))
    return out or [("image", "4:5")]


def handle_feedback(state: RuneState, feedback: CreativeFeedback) -> CreativePackage:
    cid = state["client_id"]
    brief = ClientBrief.model_validate(state["brief"])
    pkg = CreativePackage.model_validate(state["package"])
    adset = next((s for s in pkg.blueprint.adsets() if s.ref == feedback.adset_ref), None)
    mine = [c for c in pkg.creatives if c.adset_ref == feedback.adset_ref]
    if not adset or not mine:
        bus.log(cid, AGENT, f"Feedback for unknown ad set {feedback.adset_ref}; nothing to iterate on")
        return CreativePackage(client_id=pkg.client_id, blueprint=pkg.blueprint, creatives=[])

    win = feedback.winners[0] if feedback.winners else {}
    winner = next((c for c in mine if c.creative_id == win.get("creative_id")), mine[0])
    hooks = {h["id"]: HookPlus.model_validate(h) for h in state.get("hooks", [])}
    win_text = win.get("hook") or (hooks[winner.hook_id].text if winner.hook_id in hooks else winner.ad_copy.primary_text[:80])
    bus.say(cid, AGENT, "shlok.feedback", "RESPONSE",
            {"text": f"On it: {feedback.n_new} new variants iterating on “{win_text}”", "parent_id": winner.creative_id})

    losers = "; ".join(str(l.get("reason", "")) for l in feedback.losers)
    note = (f"Iterate on the WINNING hook “{win_text}”: keep its framework and promise, change the wording and the "
            f"opening visual. Insight: {feedback.insight}. Direction: {feedback.direction}. Avoid what lost: {losers}")
    research = ResearchReport.model_validate(state["research"]) if state.get("research") else None
    persona = next((p for p in (research.personas if research else []) if p.ref == adset.persona_ref), None)
    demo = demo_mode(state)
    if demo:
        new_hooks = [HookPlus(id="", adset_ref=adset.ref, text=g.text, framework="iteration")
                     for g in _fallback_hooks(brief, adset, feedback.n_new)[:feedback.n_new]]
    else:
        new_hooks = hooks_for_adset(cid, brief, persona, adset, feedback.n_new, state.get("context_doc", ""), note)

    formats, short = _formats(feedback), adset.ref[3:]
    taken_ads = {a.ref for s in pkg.blueprint.adsets() for a in s.ads} | {c.ad_ref for c in pkg.creatives}
    jobs, plans, hook_map = [], [], {}
    for k, hook in enumerate(new_hooks[:feedback.n_new], 1):
        kind, ratio = formats[(k - 1) % len(formats)]
        ad_ref = f"{winner.ad_ref}_it{k}"
        while ad_ref in taken_ads:
            ad_ref += "b"
        taken_ads.add(ad_ref)
        hook = hook.model_copy(update={"id": f"h_{short}_{new_id('f')[2:8]}"})
        hook_map[hook.id] = hook
        plans.append(AdPlan(ref=ad_ref, hook_id=hook.id, format=kind, ratios=[ratio]))
        jobs.append(Job(campaign_ref=winner.campaign_ref, adset_ref=adset.ref, ad_ref=ad_ref, hook_id=hook.id,
                        kind=kind, ratio=ratio, template_id=pick_template(hook), creative_id=new_id("cr"),
                        version=winner.version + 1, parent_id=winner.creative_id, note=feedback.direction))

    if demo:  # no generation in demo/CI runs: reuse an existing asset of the same kind and ratio
        creatives = []
        for j in jobs:
            src = next((c for c in pkg.creatives if c.kind == j.kind and c.ratio == j.ratio), winner)
            h = hook_map[j.hook_id]
            copy = fit_copy(winner.ad_copy.model_copy(update={"primary_text": h.text}), h.text, brief)
            creatives.append(src.model_copy(update={
                "creative_id": j.creative_id, "adset_ref": j.adset_ref, "ad_ref": j.ad_ref, "hook_id": j.hook_id,
                "ad_copy": copy, "landing_url": landing_url(brief, j.campaign_ref, j.ad_ref),
                "version": j.version, "parent_id": j.parent_id, "prompt": note}))
    else:
        creatives = produce(cid, brief, jobs, hook_map)

    bp = pkg.blueprint.model_copy(deep=True)
    for s in bp.adsets():
        if s.ref == adset.ref:
            s.ads.extend(p for p in plans if any(c.ad_ref == p.ref for c in creatives))
    bus.log(cid, AGENT, f"Feedback loop: {len(creatives)} new creatives for {adset.name} (parent {winner.creative_id})")
    return CreativePackage(client_id=pkg.client_id, blueprint=bp, creatives=creatives)


def apply_fixes(state: RuneState, prompts: list[FixPrompt]) -> dict:
    if not prompts or not state.get("package"):
        return {}
    cid = state["client_id"]
    brief = ClientBrief.model_validate(state["brief"])
    pkg = CreativePackage.model_validate(state["package"])
    hooks = {h["id"]: HookPlus.model_validate(h) for h in state.get("hooks", [])}

    texts = {h.id: h.text for h in hooks.values()}
    for c in pkg.creatives:  # copy limits, everywhere
        c.ad_copy = fit_copy(c.ad_copy, texts.get(c.hook_id, c.ad_copy.primary_text[:60]), brief)

    targets: dict[str, str] = {}   # creative_id -> instruction
    for p in prompts:
        refs = set(p.object_refs)
        hit = [c for c in pkg.creatives if c.creative_id in refs or c.ad_ref in refs or c.adset_ref in refs]
        if p.owner == "human" and not refs:
            hit = pkg.creatives
        for c in hit:
            targets[c.creative_id] = f"{targets.get(c.creative_id, '')} {p.prompt}".strip()
        bus.log(cid, "malav.fixer", f"Fix prompt from {p.owner}: {len(hit)} creative(s) to regenerate")

    have = {(c.ad_ref, c.ratio) for c in pkg.creatives}
    missing = [j for j in plan_jobs(pkg.blueprint, hooks) if (j.ad_ref, j.ratio) not in have]
    if demo_mode(state):
        bus.log(cid, "malav.fixer", "Demo run: applied copy limits only (no regeneration)")
        return {"package": pkg.model_dump(mode="json")}

    by_id = {c.creative_id: c for c in pkg.creatives}
    jobs = [Job(campaign_ref=c.campaign_ref, adset_ref=c.adset_ref, ad_ref=c.ad_ref, hook_id=c.hook_id, kind=c.kind,
                ratio=c.ratio, template_id=c.template_id or pick_template(hooks.get(c.hook_id) or HookPlus(id="", adset_ref="", text="")),
                creative_id=c.creative_id, version=c.version + 1, parent_id=c.parent_id, note=targets[c.creative_id][:400])
            for c in (by_id[i] for i in targets)] + missing
    if missing:
        bus.log(cid, "malav.fixer", f"Filling {len(missing)} planned asset(s) that had no creative")
    if jobs:
        for new in produce(cid, brief, jobs, hooks):
            by_id[new.creative_id] = new
    pkg = pkg.model_copy(update={"creatives": list(by_id.values())})
    write_manifest(cid, pkg)
    return {"package": pkg.model_dump(mode="json")}
