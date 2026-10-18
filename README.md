# Rune AI

Agentic digital-marketing agency: client brief → research → strategy → hooks → creatives (Malav) → budget → targeting → Meta launch → monitoring and optimization (Shlok) → Auditor → human go-live. The full design is in [PLAN.md](PLAN.md).

## Run

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt   # Windows (use .venv/bin/pip on mac/linux)
cp .env.example backend/.env                            # fill in keys
git submodule update --init                             # marketing skills
cd backend
../.venv/Scripts/python -m pytest -q                    # must pass before every push
```

Run the app (two terminals):

```bash
cd backend && ../.venv/Scripts/python -m uvicorn api:app --port 8000   # API + agents
cd web && npm install && npm run dev                                  # dashboard on http://localhost:3000
```

With `META_DRY_RUN=1` nothing is sent to Meta. On a live client, Live Performance → "Simulate next 7 days" feeds the optimizer simulated data.

FreeLLMAPI runs separately (Docker): see https://github.com/tashfeenahmed/freellmapi. Put its unified key in `backend/.env`.

## Layout

```
backend/
  core/        SHARED: schemas.py (the contract), llm.py, bus.py, approvals.py, skills.py, state.py
  graph.py     SHARED: wires malav.STEPS -> shlok.STEPS -> auditor -> go-live
  auditor/     SHARED
  malav/       Malav: intake, research, strategy, hooks, generation, QA, packaging
  shlok/       Shlok: budget, targeting, Meta launch, monitor, optimizer, feedback
  fixtures/    sample data for each handoff (one fake client: Brewbloom)
  tests/       contract and end-to-end test (CI runs it on every push/PR)
  skills/marketingskills/   git submodule
```

## How our code connects (read this before you write code)

1. **`core/schemas.py` is the contract.** Everything that crosses from Malav to Shlok (ClientBrief, ResearchReport, CampaignBlueprint, CreativePackage, CreativeFeedback) or goes to the Auditor is a Pydantic model there. You may add optional fields. Renaming or removing a field needs a PR that both of us approve (enforced by CODEOWNERS) and updated fixtures.
2. **State keys have owners** (see `core/state.py`). Malav writes `brief, research, blueprint, hooks, package`. Shlok writes `budget_plan, targeting, launch, live`. Read anything; write only your own keys.
3. **Each side exposes exactly this:**
   - `malav.pipeline`: `STEPS`, `apply_fixes(state, prompts)`, `handle_feedback(state, feedback) -> CreativePackage`
   - `shlok.pipeline`: `STEPS`, `apply_fixes(state, prompts)`, `activate(state)`
   - `shlok.budget.budget_feasibility(brief, n_adsets)`, which Malav's Strategy Agent calls
   
   `graph.py` only uses these names. To add a stage, append a `Step` to your own `STEPS`; never edit the other person's folder.
4. **Walking skeleton.** Malav's side currently has stubs that return the fixtures, so the full pipeline already runs end to end. Replace one stub at a time with the real agent and keep the test green.
5. **Fixtures are the handoff samples.** Shlok develops against `fixtures/package.json` without waiting for Malav's generators. Malav develops `handle_feedback` against `fixtures/creative_feedback.json`. If the real output changes shape, update the fixture in the same PR.
6. **CI** (`.github/workflows/ci.yml`) runs `pytest` on every push and PR. A red CI means the contract broke, so don't merge.
7. **Git flow:** branch `malav/<feature>` or `shlok/<feature>`, open a PR into `main`, merge when CI is green. Pull `main` often (`git pull --rebase origin main`).
8. **Agent visibility:** log every meaningful step with `bus.log(client_id, "malav.hooks", "text")`, and every cross-agent message with `bus.say(...)`. The UI's Agent Theater shows these.
9. **LLM calls:** always go through `core.llm.chat()` / `core.llm.structured()` with an alias (`reason`, `fast`, `creative`, `vision`). Never hardcode a provider.
10. **Human gates:** a `Step` with `gate=True` gets an approval card automatically. When a human requests changes, your step reruns with the instruction in `state["notes"][step_name]`, so read it. To ask the user something, use `core.approvals.ask_human()` at the start of a node.
