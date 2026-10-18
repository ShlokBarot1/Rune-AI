# Rune AI — Project Plan

**Rune AI** is an agentic digital-marketing agency. It takes a client from first conversation to live, self-optimizing Meta ads. Humans come in at the approval gates. Everything else is done by agents.

```
Client info ──► MALAV (Understand + Create) ──► SHLOK (Budget + Launch + Optimize) ──► AUDITOR ──► Human "Go Live"
                     ▲                                   │
                     └────── performance feedback ───────┘   (continuous loop while ads run)
```

Owners:
- **Malav** owns intake, research, strategy, hooks, generation and packaging.
- **Shlok** owns budget, targeting, Meta launch, monitoring, optimization and the feedback loop.
- **Shared:** core platform (schemas, LLM client, agent bus, approvals), the Auditor and the UI.

---

## 0. Principles (what makes this an *agentic* showcase)

Each of these must be visible in the demo, not just present in code.

| Capability | Where it shows up |
|---|---|
| **Autonomous tool use** | Agents call web search, scrapers, the Meta Ad Library, Google Trends, image/video models and the Meta Marketing API on their own. |
| **Planning** | The Strategy Agent decomposes a brief into campaigns, ad sets and ads. The Generation Planner turns that into a job graph. |
| **Reflection / self-critique** | Hook Critic, Creative QA (a vision model inspects every image) and the Auditor loop all regenerate when they reject something. |
| **Multi-agent negotiation** | Malav's Strategy Agent asks Shlok's Budget Agent "can this budget support 4 ad sets?" and gets back a counter-proposal. |
| **Closed feedback loop** | Live performance flows to the Optimizer, then to a Creative Request, then to new hooks and assets, then to the Auditor and back live. |
| **Agents ask humans** | Any agent can raise a `QUESTION` when its confidence is low. The graph pauses until the user answers. |
| **Human-in-the-loop gates** | Every pipeline stage ends in an approval card: Approve / Request changes / Reject. |
| **Suggestion evaluation** | User feedback is not applied blindly. A Suggestion Evaluator checks it against the brief, Meta policy and the budget, applies it if valid, and pushes back with reasons if not. |
| **Long-running autonomy** | The Monitor runs on a schedule for the lifetime of the campaign. |
| **Memory** | A learnings store records which hooks, angles and formats worked per industry and is reused for the next client. |
| **Observability** | The "Agent Theater" UI streams every agent's steps, tool calls and messages live. |

---

## 1. Architecture

### 1.1 Stack

| Layer | Choice | Why |
|---|---|---|
| Agent orchestration | **Python 3.12 + LangGraph** | Native `interrupt()` for human approval gates, checkpointed state (pause for days, resume), subgraphs (one per owner) and streaming events for the UI. |
| API | **FastAPI** | REST for UI actions, SSE/WebSocket for the live agent stream. |
| DB + storage + realtime | **Supabase (free tier)**: Postgres + pgvector + Storage + Realtime | One free service covers the DB, asset storage (images/videos), the vector memory and live UI updates. |
| LLM gateway | **FreeLLMAPI** (self-hosted, Docker, `http://localhost:3001/v1`) | One OpenAI-compatible endpoint over about 34 free providers, with rate-limit tracking and automatic fallback on 429/5xx. It also exposes `/images/generations`, `/videos/generations`, `/audio/speech` and embeddings. |
| Scheduler | APScheduler inside the backend (or a GitHub Actions cron that hits an endpoint) | Monitoring loop. |
| GPU worker (video) | Kaggle or Colab free GPU notebook running a small worker that polls a job table | For open-weight video models (Wan / LTX) when hosted free endpoints are rate-limited. |
| Frontend | **Next.js (App Router) + Tailwind + shadcn/ui + Recharts** | Fast to build the design in §8. |
| Meta | **Meta Marketing API** (Graph API, `facebook-business` Python SDK) with a **sandbox ad account** for development | Safe testing with no real spend. |

### 1.2 Repo layout

```
rune-ai/
  backend/
    core/                 # SHARED — agree on this in week 1, change only by mutual PR
      schemas.py          # Pydantic contracts (§2)
      llm.py              # one client → FreeLLMAPI, model aliases, JSON-retry
      bus.py              # agent message bus (§2.4)
      approvals.py        # gate helper: interrupt → UI card → resume
      suggestion_eval.py  # evaluates human change requests
      memory.py           # learnings store (pgvector)
      skills.py           # marketing-skills loader: list per agent + load_skill() tool (§3.1)
      tools/              # search, scrape, trends, ad_library, vision_qa
    malav/                # MALAV's agents + subgraph
    shlok/                # SHLOK's agents + subgraph
    auditor/              # SHARED
    skills/marketingskills/  # git submodule (coreyhaines31/marketingskills, pinned)
    graph.py              # top-level graph wiring malav → shlok → auditor → go-live
    api.py
  worker/                 # GPU generation worker (Kaggle/Colab)
  templates/              # HTML ad templates (Malav)
  web/                    # Next.js UI
  docker-compose.yml      # backend + freellmapi
```

### 1.3 Top-level graph

```
[G0 Intake] → [Research] → G2 → [Strategy ⇄ BudgetFeasibility] → G3 → [Hooks] → G4
   → [Gen Planner] → G5 → [Generate → QA loop] → [Package] → G6
   → [Budget Alloc] → G7 → [Targeting] → G8 → [Meta Build (PAUSED)]
   → [AUDITOR] ─fail→ route fix-prompt to owning agent → (back into the loop, max 3 rounds → human)
             ─pass→ G9 HUMAN GO-LIVE → [Activate] → [Monitor ⟳] → [Optimizer]
                                                              └→ [Creative Request] → Malav [Hooks…] → Auditor → live
```

`G#` marks a human approval gate. Every gate uses the same mechanism (§7).

---

## 2. Shared contracts (build these first — both people depend on them)

These Pydantic models in `core/schemas.py` are the interface between Malav and Shlok. Freeze v1 in week 1.

### 2.1 `ClientBrief` (Malav produces; everyone reads)
```python
class ClientBrief(BaseModel):
    client_id: str
    business_name: str
    website: HttpUrl | None
    industry: str
    products: list[Product]            # name, url, price, usp[], images[]
    goal: Literal["sales","leads","traffic","awareness","engagement","app_installs"]
    kpi_targets: KPITargets            # target_cpa, target_roas, target_ctr (optional)
    geo: list[str]                     # country / region codes
    languages: list[str]
    audience_notes: str
    brand: BrandKit                    # logo, colors, fonts, tone, do/don't words
    budget: BudgetInput                # total, currency, start, end, daily_cap, flexibility
    deliverables: Deliverables         # n_images, n_videos, ratios[], video_lengths[], platforms[]
    special_ad_category: Literal["none","credit","employment","housing","social_issues"]
    landing_pages: list[HttpUrl]
    constraints: list[str]             # e.g. "no discounts", "no competitor names"
```

### 2.2 `CampaignBlueprint` (Malav plans the structure; Shlok attaches money and targeting)
```
Blueprint
 └─ Campaign[]  { name, objective, funnel_stage(TOF/MOF/BOF), rationale }
     └─ AdSet[] { name, persona, angle, placements_intent, budget(None until Shlok), targeting(None until Shlok) }
         └─ AdPlan[] { hook_id, format(image/video/carousel), ratios[], copy_variant_ids[] }
```

### 2.3 `CreativePackage` (Malav → Shlok handoff)
```python
class Creative(BaseModel):
    creative_id: str
    adset_ref: str; campaign_ref: str; hook_id: str
    kind: Literal["image","video"]
    ratio: Literal["1:1","4:5","9:16","1.91:1"]
    width: int; height: int; duration_s: float | None
    storage_url: str
    copy: AdCopy        # primary_text, headline, description, cta (Meta CTA enum)
    model_used: str; template_id: str | None; prompt: str
    qa: QAResult        # score, checks[], passed
    version: int; parent_id: str | None    # lineage for regenerations
```

### 2.4 Agent bus message
```python
class AgentMessage(BaseModel):
    id: str; thread_id: str; client_id: str
    sender: str            # "shlok.optimizer", "malav.hooks", "auditor", "human"
    recipient: str
    type: Literal["REQUEST","RESPONSE","INFORM","QUESTION","FEEDBACK","FIX_PROMPT"]
    payload: dict          # validated per type
    created_at: datetime
```
The bus is stored in the `agent_messages` table and pushed to the UI through Supabase Realtime. This one table carries all inter-agent conversation (the Agent Theater renders it).

### 2.5 `AuditReport`, `BudgetPlan`, `CreativeFeedback` — defined in their sections below.

---

## 3. Free model and API stack

All text, vision, image and embedding calls go through **FreeLLMAPI**, using model *aliases* defined in config. This means swapping models never touches agent code.

| Alias | Use | Candidate free models (via the router) |
|---|---|---|
| `reason` | Strategy, budget rationale, Auditor, Suggestion Evaluator | Strongest free reasoning model available: Gemini Pro/Flash (Google AI Studio), DeepSeek / Qwen3 / GLM via OpenRouter, ModelScope or Z.ai |
| `fast` | Extraction, classification, summarizing scraped pages | Groq / Cerebras (Llama, Qwen) — very fast |
| `creative` | Hooks and ad copy | A different family from `reason`, so the critic is not grading its own style |
| `vision` | Creative QA, competitor ad analysis, product photo understanding | Gemini Flash (multimodal), Qwen-VL |
| `embed` | Memory / RAG | Router embeddings, or local `bge-small` |
| `image` | Ad visuals | **Qwen-Image** / **Qwen-Image-Edit** (ModelScope API-Inference, HF), **FLUX.1-schnell** (Cloudflare Workers AI free tier), Pollinations as last resort |
| `video` | Short video ads | **Wan 2.x** / **LTX-Video** (ModelScope, HF Spaces ZeroGPU, or the Kaggle/Colab worker) |
| `tts` | Voiceover | Edge-TTS (free) or the router's `/audio/speech` |
| `stt` | Captions | Whisper via Groq free tier |

Research and data APIs (all free or with free tiers):
- **Web search:** Tavily (free monthly credits), Jina Search, `duckduckgo-search` as fallback.
- **Page reading:** Jina Reader (`r.jina.ai/<url>`) or Crawl4AI (local, free).
- **Trends:** `pytrends` (Google Trends, unofficial); Reddit API for audience language and pain points; YouTube Data API (free quota) for comments.
- **Competitor ads:** **Meta Ad Library API** (`/ads_archive`). Limitation: for ordinary commercial ads it only returns ads delivered in the **EU**, so query with EU `ad_reached_countries`. Supplement with competitor website and social analysis.
- **Meta targeting lookup:** Targeting Search API (`/search?type=adinterest`).

`core/llm.py` must handle the realities of free models:
- Every structured call uses a Pydantic schema, JSON mode, and up to 2 repair retries that feed the validation error back to the model.
- Long research uses map-reduce chunking (free models have smaller context windows and TPM caps).
- Cache identical prompts in SQLite to save quota during development.
- Log the model, latency and tokens for every call. The UI shows this in Agent Theater.

### 3.1 Marketing Skills library (expert playbooks for the agents)

We use [coreyhaines31/marketingskills](https://github.com/coreyhaines31/marketingskills) (MIT, 50 skills, one `SKILL.md` playbook per folder) as the agents' domain knowledge. Free models are weaker marketers than frontier ones, so giving them expert playbooks closes much of that gap.

**How it plugs in:**
- Vendor the repo as a git submodule at `backend/skills/marketingskills/`. Pin a commit so updates don't silently change agent behaviour.
- `core/skills.py` loads skills with **progressive disclosure**:
  - Every agent's system prompt lists only the *names and descriptions* of the skills assigned to it.
  - The agent calls a `load_skill(name)` tool to pull the full `SKILL.md` (and any reference files) when it needs it. This keeps prompts small, which matters for free-tier context and TPM limits.
- **Foundation context:** the repo's `product-marketing` skill expects a product-marketing context doc that all other skills read first. Intake (M1) renders the approved `ClientBrief` + `ResearchReport` into exactly that doc (`clients/<id>/product-marketing.md`), so every downstream skill gets client context automatically.
- Agent Theater shows which skill each agent loaded ("Hook Agent loaded `ad-creative`, `marketing-psychology`"). Visible expert reasoning is good for the showcase.
- Each agent cites the skill rule it followed in its rationale. The Auditor checks outputs against the same skills, so generators and auditor share one standard.

**Skill → agent mapping:**

| Agent | Skills |
|---|---|
| M1 Intake | `product-marketing` (writes the context doc), `customer-research`, `offers` |
| M2 Product / Audience | `customer-research`, `product-marketing`, `pricing` |
| M2 Competitor Analyst | `competitor-profiling`, `competitors` |
| M2 Trend Scout | `social`, `content-strategy`, `marketing-ideas` |
| M3 Strategy | `ads`, `marketing-plan`, `marketing-council` (several simulated expert views on the blueprint before G3) |
| M4 Hooks + Critic | `ad-creative`, `copywriting`, `marketing-psychology`, `copy-editing` (critic) |
| M5/M6 Generation | `image`, `video`, `ad-creative` |
| M8 Packager (landing check) | `cro` (flag a weak landing page before money is spent on it) |
| S2 Budget | `ads`, `pricing` |
| S3 Targeting | `ads`, `customer-research` |
| S5 Monitor | `analytics`, `attribution` |
| S6 Optimizer | `ab-testing` (significance before declaring winners), `ads`, `marketing-loops` |
| S7 Feedback | `ad-creative`, `marketing-psychology` |
| S8 Reporting | `analytics`, `attribution` |
| Auditor | `ads`, `ad-creative`, `copy-editing`, `cro`, `marketing-psychology` |
| Suggestion Evaluator | `marketing-council` |

The skills not listed here (SEO, email, ASO and so on) are out of scope for Meta ads. They stay loadable for future channels, but no agent is assigned them now.

---

## 4. MALAV'S PART — Understand & Create

Pipeline: **client info → product analysis → hooks & ideas → generative pipeline (models, platforms, ratios, templates) → deliverables → categorize into campaigns/ad sets → hand to Shlok.**

### M1. Intake Agent (conversational onboarding) → Gate G1
**Goal:** a complete, validated `ClientBrief`.
- A chat UI paired with a live "brief card" that fills in as the conversation progresses.
- **Auto-prefill before asking anything:** given a website URL, the agent scrapes it (Jina Reader) and extracts products, prices, brand colors (from CSS and logo), tone of voice and an existing pixel if any. It then asks only for what is missing.
- It accepts uploads (product photos, brand guide PDF, proposal PDF), which are parsed and merged into the brief.
- A **slot-filling loop**: required slots are budget, dates, goal, geo, deliverables (number of images and videos, ratios, video lengths), KPIs and special ad category. The agent keeps asking until every required slot is filled with confidence ≥ 0.8. If it is unsure (for example "is this a housing ad?"), it raises a `QUESTION`.
- **Sanity checks with explanations**, for example: "₹15,000 for 30 days with 6 videos plus 3 audiences will be too thin; consider 2 audiences." It calls Shlok's `budget_feasibility` tool early (§5, S2).
- **Output:** `ClientBrief`. **G1:** the human confirms the brief.

### M2. Research Agents (run in parallel) → Gate G2
Run as a LangGraph fan-out, then a merge step.
1. **Product Analyst** — features → benefits → emotional outcomes, price positioning, objections, proof points (reviews, ratings, certifications). Sources: client site, Amazon/Flipkart/Google reviews via search and scrape, uploaded documents.
2. **Audience Researcher** — 3–5 personas, each with demographics, pains, desires, the language they use (verbatim phrases mined from Reddit, YouTube comments and reviews), and objections. Each persona carries `evidence[]` links, which is how the agent shows it did research rather than invent.
3. **Trend Scout** — Google Trends for product and category keywords (seasonality, rising queries), trending content formats and seasonal hooks (festivals, sales events in the target geo).
4. **Competitor Analyst** — finds 3–8 competitors via search and pulls their ads from the Ad Library. The `vision` model tags each ad's hook type, format, offer, CTA and visual style. Output: a competitor matrix plus *white space* (angles nobody is using).
5. **Research Synthesizer** — merges everything into a `ResearchReport` with a short executive summary, key insights and recommended angles ranked by evidence strength.

**G2:** the human reviews the Research Board. They can add or remove competitors and personas, which re-runs only the affected branch.

### M3. Strategy Agent (campaign architecture) → Gate G3
**Decides how many campaigns, ad sets and ads to run, and why.**
- Map the goal to a Meta objective (`OUTCOME_SALES`, `OUTCOME_LEADS`, `OUTCOME_TRAFFIC`, `OUTCOME_AWARENESS`, `OUTCOME_ENGAGEMENT`, `OUTCOME_APP_PROMOTION`).
- Choose funnel stages: a new account or brand gets TOF (+ MOF if a site already has traffic); retargeting (BOF) only if audiences are big enough.
- Ad sets = persona × angle combinations, ranked by research evidence.
- **Negotiation with Shlok's Budget Agent.** Strategy proposes N ad sets; Budget replies feasible / not feasible with a counter-proposal. The rule of thumb is that each ad set needs roughly 50 optimization events per week to exit Meta's learning phase:

  `min_daily_per_adset = max(account_min_daily_budget, 50 × target_CPA / 7)`

  If `total_daily < N × min_daily_per_adset`, Strategy either reduces N, merges personas, or switches to a higher-funnel optimization event. This exchange is shown as a visible conversation in Agent Theater.
- Ads per ad set: 3–6 creatives (a mix of hooks and formats) for creative testing.
- **Output:** `CampaignBlueprint` with a rationale on every node. **G3:** the human approves the tree.

### M4. Hook & Concept Agent (+ Critic) → Gate G4
- For each ad set, generate 8–12 hooks across frameworks: problem–agitate–solve, bold claim, curiosity gap, social proof, before/after (policy-safe), UGC/testimonial, listicle, "us vs them", offer-led, founder story.
- **Critic sub-agent** (a different model) scores each hook 1–10 on scroll-stop power, clarity, persona fit, brand fit and policy risk. It keeps the top 3–5 per ad set and rewrites weak ones. This is a reflection loop with at most 2 rounds.
- For each kept hook, build a **concept**: visual idea, scene description, on-screen text, and a video storyboard (shot list, 6–15 s; first 3 seconds = hook).
- **Copy variants** within Meta limits: primary text (aim for ≤125 characters visible), headline (≤40), description (≤30), and a CTA from the Meta enum (`SHOP_NOW`, `LEARN_MORE`, `SIGN_UP`, …). Translate if the brief has multiple languages.
- A policy pre-check flags personal-attribute language ("Are you overweight?"), unrealistic claims, and special-category wording.
- **The memory store is queried first:** "hooks that won for similar products in this industry" seed the generation.
- **G4:** the human sees hooks grouped by ad set and can star, edit or reject them, or type a suggestion (handled by the Suggestion Evaluator).

### M5. Generation Planner (Creative Director) → Gate G5
Turns concepts into a concrete **job list**:
- **Placements → ratios:**
  - Feed: 1:1 (1080×1080) and 4:5 (1080×1350)
  - Stories/Reels: 9:16 (1080×1920), keeping the safe zones clear (roughly the top 14% and bottom 35% free of text)
  - Link/right column: 1.91:1 (1200×628)
- **Format per concept:** static image, carousel or video, matching the client's requested numbers of images and videos.
- **Model routing per job:**
  - Photoreal lifestyle scene → Qwen-Image
  - Edit the real product photo into a scene → Qwen-Image-Edit, or background removal (`rembg`) + composite
  - Fast drafts → FLUX-schnell
  - Video → Wan/LTX for short generative clips, or **template motion** (below)
- **Template choice** from `templates/`. Premade HTML/CSS ad layouts: product hero, offer badge, testimonial card, comparison split, UGC caption style, listicle, before/after.
- An estimate of total jobs, expected duration and free-quota usage.
- **G5:** the human approves the plan (quantities, ratios, models).

### M6. Generation Pipeline
Key design rule: **never let diffusion models render the text, logo or product label.** They garble text and change products. Instead:
1. **Background/scene layer:** generated by the image model (prompt built from concept + brand palette + negative prompt).
2. **Product layer:** the client's real product photo, with background removed and composited in (with relighting via Qwen-Image-Edit if needed). This keeps the product accurate.
3. **Text/brand layer:** an HTML template rendered with the hook, copy, logo, brand fonts/colors and CTA button, then screenshotted to PNG with **Playwright** at the exact pixel size per ratio. One concept renders to all ratios automatically.
4. **Video path:**
   - **A — Generative:** Wan/LTX image-to-video from the composed key frame (3–5 s clips), stitched.
   - **B — Template motion (reliable default):** FFmpeg (or Remotion) animates layers with zoom/pan, text pop-ins and a product reveal, adds an Edge-TTS voiceover, burns in Whisper-generated captions and adds royalty-free music. Always works, even when GPU quota runs out.
   - The planner picks A when quota is available and falls back to B automatically.
5. Jobs run in a queue (the `generation_jobs` table). The GPU worker on Kaggle/Colab polls it for video jobs.

### M7. Creative QA Agent (vision self-check loop)
Every asset is inspected by the `vision` model plus deterministic checks:
- Exact pixel dimensions and ratio; file size and length within Meta limits.
- Text legibility and contrast (WCAG contrast computed on the template layer).
- Safe-zone violations for 9:16.
- Product fidelity: does the product look like the reference photo?
- Brand colors present; no garbled text or artifacts, no extra fingers or faces in odd places.
- Hook visible in the first frame or first 3 seconds of video.
- A policy smell test.

**Fail → automatic regeneration** with the QA notes appended to the prompt (max 2 retries). Otherwise the asset is marked `needs_human`. The QA score is stored on the creative.

### M8. Packager → Gate G6
- Groups creatives into **campaign → ad set → ad** following the blueprint. Each ad = creative(s) + copy variant + CTA + landing URL with UTMs (`utm_source=meta&utm_campaign=…&utm_content=<creative_id>`).
- Produces a `CreativePackage` plus a `manifest.json` and a downloadable ZIP (the deliverables).
- **G6:** a gallery review per ad set, with ratio tabs. Per asset the human can approve, regenerate with a note, or swap the hook.
- On approval it sends an `INFORM` to `shlok.budget` and Shlok's subgraph starts.

### Malav's deliverables checklist
- [ ] Intake chat + website auto-prefill + slot-filling + `QUESTION` support
- [ ] 5 research agents + Research Board
- [ ] Strategy Agent + feasibility negotiation with Shlok
- [ ] Hook generator + Critic loop + copy within Meta limits
- [ ] Generation Planner + 6–8 HTML templates
- [ ] Image pipeline (scene + product composite + template text)
- [ ] Video pipeline (template-motion default + generative path)
- [ ] Vision QA loop
- [ ] Packager + manifest + ZIP
- [ ] Handler for `CreativeFeedback` from Shlok (§6)

---

## 5. SHLOK'S PART — Budget, Launch, Track & Optimize

Pipeline: **read the client's budget/proposal → allocate budget → targeting & placements → build on Meta (paused) → audit → go live → monitor → optimize → feed back to Malav.**

### S1. Budget Intake / Proposal Parser
- Reads `ClientBrief.budget` plus any uploaded proposal (PDF parsed to text, then the `fast` model extracts the fields).
- Normalizes total budget, currency, flight dates, daily cap, flexibility (± %), KPI targets, and any channel or split constraints the client gave.
- Reads ad account facts from the API: currency, timezone, `min_daily_budget`, spend cap, existing pixel/dataset and conversion events.
- Unclear items (e.g. "is the ₹50k including GST/agency fee?") become `QUESTION`s to the human.

### S2. Budget Allocation Agent → Gate G7
Deterministic math with an LLM-written rationale. The math must not be done by the LLM.
- **`budget_feasibility(total, days, target_cpa, n_adsets)`** — exposed as a tool that Malav's Strategy Agent calls (§M3).
- **Funnel split defaults** (overridable): new account ≈ TOF 70 / MOF 20 / BOF 10; warm account ≈ 50 / 30 / 20. BOF is capped by retargeting audience size.
- **Testing reserve:** about 15–20% held for creative testing and for new creatives from the feedback loop.
- **CBO vs ABO:** use ABO (ad-set budgets) in the testing phase so every angle gets fair spend; switch to CBO / Advantage campaign budget for scaling winners. The agent explains which it chose.
- **Bid strategy:** `LOWEST_COST_WITHOUT_CAP` by default; `COST_CAP` / `ROAS` goal only if the client gave reliable targets.
- **Pacing:** daily vs lifetime budget, and ad scheduling if the client wants it.
- **Invariants checked in code:** Σ allocations ≤ client budget; every ad set ≥ its minimum; currency in minor units (Meta takes budgets in cents/paise).
- **Output:** `BudgetPlan` per campaign and ad set, with a projection (estimated results range from the target CPA). **G7:** the human approves, using an allocator UI with sliders that re-validate live.

### S3. Targeting & Placement Agent → Gate G8
- Persona → Meta `targeting` spec: `geo_locations`, `age_min/max`, `genders`, and `flexible_spec` interests and behaviors looked up via the Targeting Search API (names resolved to real IDs). Languages via `locales`.
- Decides between Advantage+ audience (broad, recommended for most cases) and manual detailed targeting, and explains why.
- Placements: Advantage+ placements by default. Manual placements only if the creatives lack a ratio (for example, no 9:16 means Reels and Stories are excluded). It cross-checks against the `CreativePackage` ratios.
- Special ad categories: enforces the restricted targeting rules (no age/gender/ZIP narrowing for housing, credit or employment).
- Exclusions: existing customers and purchasers if a pixel or custom audiences exist.
- **G8:** the human approves the targeting summary (shown as plain English plus a raw JSON toggle).

### S4. Meta Launch Agent (Marketing API)
Builds everything **in `PAUSED` status**. Nothing spends until the Auditor passes and the human clicks Go Live.
1. Upload images → `POST /act_{id}/adimages` (get `hash`). Upload videos → `POST /act_{id}/advideos`, then poll until `status.video_status == ready`.
2. `POST /act_{id}/campaigns` — name, objective, `special_ad_categories`, status PAUSED (and the budget here if CBO).
3. `POST /act_{id}/adsets` — budget (if ABO), `billing_event`, `optimization_goal`, `bid_strategy`, `targeting`, placements, `promoted_object` (pixel + event), start/end time.
4. `POST /act_{id}/adcreatives` — `object_story_spec` (page_id, link_data / video_data, message, headline, CTA, link with UTMs). For multi-ratio creatives use placement asset customization (`asset_feed_spec`) so the 9:16 asset serves in Stories/Reels and 4:5 in Feed.
5. `POST /act_{id}/ads` — ad set + creative, status PAUSED.

Engineering rules:
- **Idempotent:** a local `meta_objects` table maps local IDs to Meta IDs. A rerun skips anything already created.
- **Partial-failure rollback:** if step N fails, delete or archive what was created in this run.
- **Dry-run mode:** prints the exact payloads without calling the API. Used for the Auditor and for demos without network.
- Respect Marketing API rate limits (read the `x-business-use-case-usage` header and back off).
- After creation, read back `effective_status` and `ad_review_feedback`. Disapprovals become a FIX_PROMPT to Malav.

### S5. Monitor Agent (scheduled, long-running)
- Runs every 3–6 hours, plus once at the end of each day, per active client.
- Pulls the **Insights API** at ad / ad set / campaign level: spend, impressions, reach, frequency, clicks, CTR, CPC, CPM, results, cost per result, purchase value → ROAS. For video: hook rate = 3-second views ÷ impressions; hold rate = ThruPlays ÷ 3-second views.
- Stores a time series in the `insights` table.
- **Detectors** (rules first, the LLM explains them):
  - Pacing: spend vs plan ± 20%
  - Zero delivery after 24 h
  - Disapproved or limited ads
  - Frequency > 3 (fatigue)
  - CTR drop > 30% vs the 3-day baseline
  - CPA > 1.5× target after enough spend
  - Learning status ("Learning limited")
- Each finding becomes an `INFORM` to the Optimizer.

### S6. Optimization Agent
Proposes **actions** with a risk class.

| Action | Rule (examples) | Execution |
|---|---|---|
| Pause loser ad | spend ≥ 2× target CPA and 0 conversions, or CTR < 50% of ad-set median after ≥ 1,000 impressions | Auto (low risk, logged) |
| Scale winner | CPA ≤ 0.8× target for 3 days → budget +15–20% (never more than ~20% per 48–72 h, to avoid resetting learning) | Approval |
| Reallocate | move budget from losing to winning ad sets inside the same total | Approval |
| Refresh creative | fatigue (frequency > 3, CTR decaying) | → Creative Request to Malav |
| New angle test | a persona with strong CTR but weak conversions → test an offer-led angle | → Creative Request |
| Kill ad set | persistent underperformance after the learning phase | Approval |

- Guardrails in code: a max daily budget change, never exceeding the client budget, and no edits during the first 48 h of learning unless something is broken.
- Every action is written to an **action log** with before/after metrics, so its impact can be measured later.

### S7. Creative Feedback Agent (the loop to Malav)
Packs the data into a structured `CreativeFeedback`:
```json
{
  "adset_ref": "TOF-Persona2-Convenience",
  "winners": [{"creative_id": "c12", "hook": "…", "hook_rate": 0.34, "ctr": 0.021, "cpa": 210}],
  "losers":  [{"creative_id": "c15", "reason": "low hook rate 0.09, first frame text-heavy"}],
  "insight": "UGC-style + price-in-first-3s outperforms polished product shots 2.3×",
  "request": {"n_new": 3, "formats": ["video 9:16"], "direction": "more UGC, keep price hook, new opening visuals"}
}
```
This is sent as a `REQUEST` to `malav.hooks`. Malav's agents generate variants (lineage tracked via `parent_id`), the Auditor reviews them, the human gives quick approval, and the Launch Agent adds them to the existing ad set.
Winning patterns are also written to the **memory store** for future clients.

### S8. Reporting Agent
- A daily summary in the UI, plus a weekly client report (PDF/HTML): spend vs plan, results, the best creatives with thumbnails, actions taken and why, and next steps. Written in plain language.

### Performance Simulator (needed for the demo)
Sandbox ad accounts **do not deliver ads**, so the monitor would only ever see zeros. Build `shlok/simulator.py`. It generates realistic insights per ad (base CTR/CVR drawn per creative, fatigue curves over time, noise, a few clear winners and losers) and writes them into the same `insights` table. A toggle switches between `source: meta | simulated`. This lets the full optimize → feedback → regenerate loop run in a live demo.

### Shlok's deliverables checklist
- [ ] Budget/proposal parser + account facts reader
- [ ] `budget_feasibility` tool (used by Malav) + allocation engine with invariants + test
- [ ] Targeting resolver (Targeting Search API) + placement logic
- [ ] Launch Agent: upload, campaign, ad set, creative, ad (PAUSED), idempotency, rollback, dry-run
- [ ] Monitor + detectors + insights store
- [ ] Optimizer with risk classes + guardrails + action log
- [ ] Creative Feedback → Malav loop
- [ ] Performance Simulator
- [ ] Reporting Agent

---

## 6. The Malav ⇄ Shlok communication loop

All communication goes over the agent bus (§2.4) and is visible in Agent Theater.

| From → To | Type | When |
|---|---|---|
| malav.strategy → shlok.budget | REQUEST `budget_feasibility` | Before G3 |
| shlok.budget → malav.strategy | RESPONSE (feasible / counter-proposal) | Same |
| shlok.targeting → malav.planner | QUESTION "no 9:16 assets for persona 3 — generate, or exclude Reels?" | Before launch |
| shlok.launch → malav.hooks | FIX_PROMPT (Meta disapproval reason) | After review |
| shlok.feedback → malav.hooks | REQUEST CreativeFeedback | While live |
| malav.packager → shlok.launch | INFORM new creatives ready | After regeneration |
| auditor → any | FIX_PROMPT | On audit fail |
| any → human | QUESTION | Low confidence |

Loop protection: each thread has a max-rounds counter (3). After that it escalates to the human.

---

## 7. Human-in-the-loop design (shared, `core/approvals.py`)

Each gate:
1. The agent finishes its stage, then calls `interrupt({gate, summary, artifact_ref, confidence, open_questions})`. The graph checkpoints and pauses.
2. The UI shows an **Approval card** in the Approvals inbox: a summary, the artifact view, the agent's rationale and three actions.
   - **Approve** → resume.
   - **Request changes** (free text) → **Suggestion Evaluator**:
     - The `reason` model checks the suggestion against the brief, research evidence, Meta policy and budget invariants.
     - **Valid** → the change is applied, the affected node reruns, and a diff of what changed is shown.
     - **Partly valid or risky** → the agent replies with its reasoning and an alternative ("Switching all budget to Reels would starve the feed ad set below Meta's minimum; I suggest 60/40 instead. Apply?") and waits for confirmation.
     - **Invalid** (policy violation, impossible math) → it declines with an explanation. The human can still force it, except where it would violate Meta policy.
   - **Reject** → the stage reruns from scratch with the rejection reason as context.
3. **Agent-initiated questions** use the same mechanism. A `QUESTION` pauses only that branch, so other parallel branches keep working.

Gates: G1 brief · G2 research · G3 blueprint · G4 hooks · G5 generation plan · G6 creatives · G7 budget · G8 targeting · **G9 final Go-Live (after the Auditor)** · plus quick approvals for optimizer actions marked "Approval".

---

## 8. AUDITOR AGENT (shared ownership)

Runs after the Meta objects are built (PAUSED) and before G9. It also runs on every creative refresh while live.

It uses the strongest `reason` model, **from a different family than the generators**, and works through a rubric. Each dimension is scored 0–100 with findings attached to specific objects.

| Dimension | Checks |
|---|---|
| **Brief alignment** | Objective matches the goal; geo, languages and constraints honored; deliverable counts and ratios match what the client asked for. |
| **Strategy coherence** | Every ad set maps to a researched persona and angle; no overlapping or duplicate audiences; funnel logic makes sense. |
| **Creative quality** | Re-runs vision QA on a sample; hook strength; brand consistency; ratio coverage per placement; safe zones; product fidelity. |
| **Copy & policy** | Character limits; CTA valid; Meta Advertising Standards (personal attributes, misleading claims, before/after, prohibited content, special-category wording); landing URL returns 200 and matches the ad's claim/offer; UTMs present. |
| **Budget** | Σ ≤ client budget; per-ad-set minimums; feasibility vs target CPA; testing reserve present; bid strategy is consistent with the KPI data available; currency/minor units correct. |
| **Targeting & placement** | Valid interest IDs; special-category restrictions; placements match the available ratios; no conflicting exclusions. |
| **Technical** | Every ad PAUSED and linked correctly; creative ↔ ad set mapping matches the manifest; pixel/event set for conversion objectives. |

Deterministic checks (math, sizes, URLs, char limits, IDs) run as code. Judgment checks are done by the LLM.

**Output — `AuditReport`:**
```python
class AuditReport(BaseModel):
    decision: Literal["PASS","PASS_WITH_NOTES","FAIL"]
    scores: dict[str, int]
    findings: list[Finding]        # severity (blocker/major/minor), object_ref, owner ("malav.hooks"...), evidence
    fix_prompts: list[FixPrompt]   # one per owning agent, ready to execute
```
- **FAIL** (any blocker, or overall < 70) → each `FixPrompt` is sent to its owning agent, e.g. *"Ad set TOF-P2 ad 3: headline 'Lose 10kg in 7 days' violates the unrealistic-claims policy. Regenerate 2 alternatives using the convenience angle from research insight #4; keep the 4:5 and 9:16 ratios."* Owners fix, the Auditor re-audits only the changed objects. Max 3 rounds, then the human sees the report and decides.
- **PASS / PASS_WITH_NOTES** → G9: the human sees the audit report plus a final preview of every ad, then clicks **Go Live**. The Launch Agent sets status `ACTIVE` and the Monitor schedule starts.

---

## 9. UI — design system (from the reference "Payno" dashboard)

### 9.1 Design DNA
- **Feel:** airy, calm, frosted. A pale cool-grey canvas, white translucent cards, and one muted navy accent. A soft blue mist gradient bleeds from the top center. Lots of whitespace, small quiet labels and large light numerals.
- **Font:** SF Pro.
  - Stack: `font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", system-ui, sans-serif;`
  - Licensing note: Apple's license doesn't permit self-hosting SF Pro on the web. On Apple devices the stack above renders real SF Pro. Elsewhere it falls back to **Inter**, which is the closest match. Do not bundle the SF font files.

### 9.2 Tokens (`web/app/globals.css`)
```css
:root {
  --bg: #E9ECF1;            /* outer page */
  --canvas: #F2F4F7;        /* app surface */
  --card: rgba(255,255,255,0.72);
  --card-solid: #FFFFFF;
  --card-border: rgba(255,255,255,0.9);
  --tile: #F5F7FA;          /* inner tiles inside cards */
  --text: #1B1F27;
  --text-2: #7D8696;        /* labels */
  --text-3: #B3BAC5;        /* hints, axis */
  --primary: #45608A;       /* Continue button, active bar */
  --primary-ink: #1F2D4D;   /* logo circle, active rail icon */
  --mist: #9DB2CF;          /* header gradient */
  --success-bg: #DDF4E4; --success: #2F9B5A;
  --danger-bg:  #FCE4E2; --danger:  #D4554B;
  --radius-card: 20px; --radius-tile: 14px; --radius-pill: 999px;
  --shadow: 0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.06);
}
.card { background: var(--card); backdrop-filter: blur(20px); border: 1px solid var(--card-border);
        border-radius: var(--radius-card); box-shadow: var(--shadow); padding: 20px; }
.mist { background: radial-gradient(60% 50% at 50% 0%, var(--mist) 0%, transparent 70%); }
```

### 9.3 Type scale
| Role | Size / weight / tracking |
|---|---|
| Hero numerals (e.g. $56,751.23) | 34–40px / 400 / -0.02em, tabular-nums |
| Card numerals (e.g. $19,270.56) | 22–24px / 500 / -0.01em |
| Card title ("Recent transactions") | 13px / 500, `--text` |
| Label ("Available Balance") | 11–12px / 500, `--text-2` |
| Meta text (timestamps, "Due in 3 days") | 11px / 400, `--text-3` |
| Button | 13px / 500 |

### 9.4 Components (as they appear in the reference)
- **Left icon rail:** 56px wide, circular 36px white buttons, active = filled `--primary-ink` with a white icon; settings and logout pinned to the bottom.
- **Top bar:** logo (navy circle + wordmark "rune") left; search, bell and avatar as circular buttons right; the mist gradient sits behind.
- **Quick-action circles:** 40px white circles with an icon and a small label below.
- **List rows:** avatar/icon (32px circle), title + sub-label, amount right-aligned, with a sign colour if needed.
- **Pill badges:** small green/red pills for deltas (+4.2%).
- **Mini bar chart:** thin rounded bars, the active bar in `--primary` with a dot marker, the rest in `#E3E8EF`.
- **Primary button:** full-width pill, `--primary`, white text.
- **Gradient card** (from the credit card): white → `#4F6A93` diagonal, used for creative previews and the "campaign card".
- **Animation:** 150–200 ms ease-out on hover and lift; numbers count up on load; agent stream items fade/slide in.

### 9.5 Screens (mapping the reference layout to Rune)
1. **Command Center (home)** — mirrors the reference 1:1:
   - "Available Balance" → **Total Budget Remaining** (with "Spent today · ₹x" chip). Quick actions: *New Client, Run Research, Generate, Launch*.
   - "Total Revenue / Monthly Expense" → **Revenue (ROAS) / Ad Spend** cards with delta pills.
   - "Earning Activity" → **Results this week** bar chart.
   - "Recent transactions" → **Agent Activity** feed (agent avatar, action, time, impact like "+₹1.2k moved").
   - "Upcoming Payments" → **Pending Approvals** (gate name, client, "Due" = waiting since).
   - "Send Money" panel → **Quick Budget Shift** (from ad set → to ad set, amount, "Estimated impact", Continue → approval).
   - "Credit Card" → **Top Creative** live card (gradient card holding the best-performing ad).
2. **Client Onboarding** — chat left, live brief card right (fields fill in with a check animation).
3. **Research Board** — persona cards, competitor matrix with ad thumbnails, trend chart, insights with evidence links.
4. **Strategy Blueprint** — an expandable campaign → ad set → ad tree, rationale on hover, and the feasibility negotiation transcript.
5. **Hook Lab** — hook cards with critic scores, star/edit/reject.
6. **Creative Studio** — gallery grouped by ad set; ratio tabs (1:1 · 4:5 · 9:16 · 1.91:1); per-asset QA score, lineage (v1 → v2) and "Regenerate with note".
7. **Budget & Targeting** — allocator sliders with live invariant checks; targeting summary in plain English.
8. **Audit Report** — dimension score rings, findings by severity, fix-prompt status, then the **Go Live** button.
9. **Live Performance** — KPI cards, per-ad table, fatigue indicators, optimizer action log with before/after.
10. **Agent Theater** — a live graph of the pipeline (nodes light up as agents run) plus a streaming message log of the agent bus, with model and token usage per call. This is the main screen for showing off the agentic side.
11. **Approvals Inbox** — all pending gates and questions in one place.

---

## 10. Data model (SQLite tonight; Supabase Postgres later)

`clients`, `briefs` (versioned JSON), `research_reports`, `blueprints`, `hooks`, `generation_jobs`, `creatives` (with `parent_id` and `version`), `packages`, `budget_plans`, `targeting_specs`, `meta_objects` (local_id ↔ meta_id, type, status), `insights` (ts, level, object_id, metrics JSON, source), `optimizer_actions`, `agent_messages`, `approvals` (gate, status, payload, decision, comment), `audits`, `learnings` (text + embedding vector + industry + metrics), plus the LangGraph checkpoint tables.

---

## 11. Tonight's build (one night, ~10 hours, two people in parallel)

The architecture above stays the same. Tonight we build the **demo-complete version**: every agent and every gate exists and the full loop runs end to end, but each piece is the simplest version that works. The cuts are listed below the schedule; they can be upgraded later without changing the contracts.

### Before starting (first 30 min, do these in parallel — they involve waiting on other services)
- **Shlok:**
  - Create a Meta developer app with the Marketing API product.
  - Create a **sandbox ad account** and generate a long-lived access token.
  - Make sure a **Facebook Page** exists, because ad creatives need a `page_id`.
  - Request Ad Library API access. Identity confirmation can take longer than tonight; if it isn't approved, the Competitor Analyst falls back to web search.
- **Malav:** get free API keys for Google AI Studio (Gemini), Groq, OpenRouter, Cerebras, ModelScope and Cloudflare Workers AI, plus Tavily and Jina. Start FreeLLMAPI in Docker and add the keys. Test one chat call, one vision call and one image call.

### Schedule

| Time | Malav | Shlok |
|---|---|---|
| **0:00–1:00** (together) | Repo skeleton; `schemas.py` frozen (§2); `llm.py` (FreeLLMAPI client + JSON retry); `skills.py` + marketingskills submodule | `bus.py` + `approvals.py` (LangGraph `interrupt` + SQLite checkpointer); FastAPI + SSE stream; Next.js app with design tokens (§9.2) and the shell (rail, top bar, mist) |
| **1:00–4:00** | M1 Intake (chat + website prefill + slot filling) → M2 research (3 agents in parallel) → M3 Strategy (calls `budget_feasibility`) → M4 Hooks + Critic | S1 budget parser → S2 allocation engine + `budget_feasibility` tool (+ one assert-based test of the invariants) → S3 targeting resolver → S4 Launch Agent (dry-run first, then sandbox, all PAUSED) |
| **4:00–7:00** | M5 planner → M6 image pipeline (scene from Qwen-Image/FLUX + product cut-out + 3 HTML templates rendered with Playwright for all 4 ratios) → template-motion video (FFmpeg + Edge-TTS) → M7 vision QA with 1 automatic retry → M8 packager | S5 Monitor + **Performance Simulator** → S6 Optimizer (rules + guardrails + action log) → S7 Creative Feedback to Malav → S8 daily summary |
| **7:00–8:30** (together) | **Auditor** (deterministic checks + LLM rubric + fix prompts routed to owners) and the Suggestion Evaluator; wire the top-level graph; run the full loop end to end | Same |
| **8:30–9:30** | UI: Onboarding chat, Creative Studio, Audit Report | UI: Command Center, Approvals Inbox, Agent Theater, Live Performance |
| **9:30–10:00** (together) | Run the full demo script (§13) twice with the cache on; record a backup video of a full run | Same |

**Integration rule:** each person builds their subgraph to run standalone on fixture JSON. Malav keeps a `fixtures/creative_package.json` for Shlok; Shlok keeps a `fixtures/creative_feedback.json` for Malav. Nobody waits on the other.

### Cut for tonight (upgrade later; contracts don't change)

| Full plan | Tonight |
|---|---|
| Supabase (Postgres, Storage, Realtime) | **SQLite** + a local `media/` folder + SSE from FastAPI. Zero setup. |
| 5 research agents | **3**: Product+Audience, Competitor, Trends (the synthesizer is the merge step) |
| 6–8 ad templates | **3**: product hero, offer badge, UGC caption |
| Generative video (Wan/LTX) + Kaggle GPU worker | **Template-motion video only.** Try one hosted Wan/LTX call through FreeLLMAPI `/videos/generations`; if it works it's a bonus. |
| pgvector memory | A `learnings` table; look up by industry string, no embeddings |
| 11 screens | **7**: Command Center, Onboarding, Creative Studio, Approvals Inbox, Audit Report, Live Performance, Agent Theater. Research, blueprint and hooks are shown inside their approval cards. |
| Weekly PDF reports | Daily summary text in the UI |
| Real ad delivery | Sandbox (PAUSED objects visible in Ads Manager) + Simulator for performance |

**Kept in full** (these are what make the project agentic): every agent role, all 9 approval gates, agent questions to the user, the Suggestion Evaluator, the Strategy ⇄ Budget negotiation, the vision QA retry loop, the Auditor fix loop, and the Optimizer → Malav feedback loop.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Free-tier rate limits or outages | FreeLLMAPI fallback chains; prompt cache; aliases let us swap models; the demo runs from cached runs if needed. |
| Free models produce broken JSON | Pydantic validation + repair retries + small focused prompts. |
| Diffusion garbles text or the product | Text/logo via HTML templates; real product photo composited; vision QA. |
| Free video generation is slow or unavailable | Template-motion video (FFmpeg + TTS + captions) is the default; generative video is a bonus. |
| Sandbox ads don't deliver, so there is no live data | Performance Simulator; real ad account only for an optional small live test. |
| Meta app review / Standard Access | Development access is enough for your own and sandbox ad accounts. Request Standard Access only if serving real clients. |
| Ad Library API covers EU commercial ads only | Query EU countries; supplement with competitor site and social analysis. |
| Accidental spend | Everything is created PAUSED; only the G9 human click activates; budget invariants and change caps live in code. |
| Agent loops never converge | Max rounds per thread (3), then escalate to the human. |
| SF Pro licensing on the web | System font stack, Inter fallback, no bundled SF files. |

---

## 13. Demo script (about 10 minutes)

1. Paste a real D2C brand URL. Intake prefills 70% of the brief and asks 4 smart questions. **G1**.
2. Agent Theater: 4 research agents run in parallel; the competitor analyst shows real Ad Library ads. **G2**.
3. Strategy proposes 4 ad sets; Shlok's Budget Agent pushes back ("budget supports 3"); they settle live. **G3**.
4. Hooks with critic scores; the user types a bad suggestion ("say 'guaranteed results'"), and the agent politely declines, citing policy. **G4**.
5. Creatives generated across 4 ratios; one fails QA and auto-regenerates on screen. **G5/G6**.
6. Budget allocator + targeting. **G7/G8**. Objects created PAUSED in the sandbox (show Ads Manager).
7. The Auditor finds 1 blocker, sends a fix prompt, the fix lands and re-audits to PASS. **G9 Go Live**.
8. Switch to the simulator, fast-forward 5 days. The Monitor flags fatigue, the Optimizer pauses losers and proposes scaling, and the feedback agent asks Malav's agents for 3 new UGC variants. They're generated, audited and added.
9. Weekly client report generated.
