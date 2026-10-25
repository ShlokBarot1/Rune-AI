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

### What the ads show (any kind of client)

Intake decides per campaign (`ClientBrief.creative_focus`), from the website and notes, and asks once when unsure:

| Focus | For | Ads |
|---|---|---|
| `product` | D2C, catalogs | the real product photo on a generated scene; each ad set features its own product |
| `range` | category / range campaigns ("our new skincare line") | line-ups of up to 4 products + benefit tags, and carousels with a card per product (own name, facts, link) |
| `photos` | services, local businesses, apps, events | the client's own photos as the image, nothing generated |
| `brand` | pure awareness, causes | generated lifestyle scenes, no product |

For a range, intake reads the brand's listing and product pages (including shop subdomains) for names, facts, prices and packshots, and asks for photos it can't find. Prices only come from a product's own page, never from search snippets.

### Real-world campaign shapes

| Need | How it works | Where |
|---|---|---|
| **Local businesses** | Locations on the brief (address, radius, pincodes) are geocoded (OpenStreetMap) and targeted as radius / postcode / city around each place; copy speaks to the neighbourhood. Special ad categories get Meta's 25 km minimum and no postcodes | `ClientBrief.locations`, `shlok/targeting.py` |
| **Offers & sales** | `ClientBrief.offer` (deal, was/now, coupon, last day) is drawn on the ads, written into the copy, and its ads pause by themselves after the last day | `media.offer_html`, `live.offer_ended` |
| **Festivals & sale days** | A per-country calendar (Diwali, Eid, Black Friday, Mother's Day…) feeds research, copy and **budget boosts**: the testing reserve is spent on the peak days as Meta budget schedules, so the total never moves | `core/calendar.py`, `budget.peak_boosts` |
| **Audiences** | Retargeting ad set (site visitors + Page/Instagram engagers) running the best ad set's creatives; buyers and the uploaded customer list excluded from prospecting; a lookalike ad set. Customer CSVs are SHA-256 hashed before they leave the server | `shlok/audiences.py` (needs `META_PIXEL_ID`, `META_PAGE_ID`, optional `META_IG_ACCOUNT_ID`) |
| **Account history** | A brand that advertised before: its past winners and CPA feed research and the CPA target | `shlok/history.py` |
| **Languages** | Ad sets take turns over `brief.languages`; copy, on-image labels, Google web fonts per script (Devanagari, Tamil, Arabic RTL…), voiceover voice, research queries and Meta locales follow | `core/langs.py` |
| **Catalogs** | Shopify / WooCommerce product APIs read in seconds (exact prices + currency); with 4+ priced products an Advantage+ catalog campaign is added (items uploaded with `items_batch`, a product set, a dynamic ad) | `lineup.shop_feed`, `shlok/catalog.py` (`META_BUSINESS_ID` or `META_CATALOG_ID`) |
| **Restricted categories** | Alcohol, gambling, crypto, finance, pharma, weight loss, dating, politics, jobs, housing, tobacco… detected from the brief: age floors, copy rules, Meta special categories, and go-live blocked until authorisation is confirmed (forbidden ones always blocked) | `core/compliance.py` |
| **Money safety** | Each Meta campaign gets a hard `spend_cap`; the monitor pauses everything at 95% of budget, alerts on overspend days and CPA spikes, and turns Meta disapprovals into a one-click rewrite + relaunch | `live.safety` |
| **Agency mode** | `RUNE_AUTH=1`: sign-in, roles (owner / strategist / client), per-campaign access, a client portal (`/portal`), white-label branding (`/settings`), weekly HTML reports and expiring share links | `core/auth.py`, `agency.py` |

### Client's own creatives (no generative media)

On **New campaign**, pick **I have my own** and add the client's finished images/videos (PNG, JPG, WEBP, MP4, MOV, WEBM), optionally with a headline and primary text per file. The run then:

- skips the deliverables question (the uploads are the deliverables) and plans exactly one ad per file, in that file's own ratio
- never calls an image/video model, product cutout or voiceover; files are only checked (size, shape, video length, cover frame) and, when their shape isn't a Meta ratio, letterboxed onto the nearest one (never cropped)
- uses the client's copy where given, otherwise the hooks agent writes it; both stay editable at the hooks and creatives gates
- in the live loop, asks the client for fresh files instead of generating them: **Live Performance → Add your own creatives** builds them paused, audits them, and launches after you approve

API: `POST /malav/runs/{cid}/upload?filename=x.mp4` (raw file body) returns a path; pass `{"intake": {"website": ..., "creatives": [{"path": ..., "headline": ...}]}}` to `POST /runs` (with the same `client_id`). While live: `POST /malav/runs/{cid}/creatives {"adset_ref", "creatives"}`.

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
