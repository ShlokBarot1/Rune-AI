"""Shared contracts between Malav's agents, Shlok's agents and the Auditor.

This file is the integration boundary. Change it only through a PR that both
owners approve (see CODEOWNERS). Add optional fields freely; never rename or
remove a field without updating the fixtures and both sides in the same PR.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def now() -> datetime:
    return datetime.now(timezone.utc)


Goal = Literal["sales", "leads", "traffic", "awareness", "engagement", "app_installs"]
Objective = Literal[
    "OUTCOME_SALES", "OUTCOME_LEADS", "OUTCOME_TRAFFIC",
    "OUTCOME_AWARENESS", "OUTCOME_ENGAGEMENT", "OUTCOME_APP_PROMOTION",
]
GOAL_TO_OBJECTIVE: dict[str, str] = {
    "sales": "OUTCOME_SALES", "leads": "OUTCOME_LEADS", "traffic": "OUTCOME_TRAFFIC",
    "awareness": "OUTCOME_AWARENESS", "engagement": "OUTCOME_ENGAGEMENT",
    "app_installs": "OUTCOME_APP_PROMOTION",
}
Ratio = Literal["1:1", "4:5", "9:16", "1.91:1"]
RATIO_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920), "1.91:1": (1200, 628),
}
CTA = Literal[
    "SHOP_NOW", "LEARN_MORE", "SIGN_UP", "ORDER_NOW", "GET_OFFER", "BOOK_NOW",
    "CONTACT_US", "DOWNLOAD", "SUBSCRIBE", "APPLY_NOW",
]
FunnelStage = Literal["TOF", "MOF", "BOF"]
SpecialAdCategory = Literal["none", "credit", "employment", "housing", "social_issues"]


# ---------- Client brief (Malav: M1 Intake) ----------

class Product(BaseModel):
    name: str
    url: Optional[str] = None
    price: Optional[float] = None
    usp: list[str] = []
    images: list[str] = []          # local paths or URLs of real product photos


class KPITargets(BaseModel):
    target_cpa: Optional[float] = None   # in budget currency, major units
    target_roas: Optional[float] = None
    target_ctr: Optional[float] = None   # 0.01 == 1%


class BrandKit(BaseModel):
    logo: Optional[str] = None
    colors: list[str] = []          # hex
    fonts: list[str] = []
    tone: str = ""
    do_words: list[str] = []
    dont_words: list[str] = []


class BudgetInput(BaseModel):
    total: float                    # major units (e.g. rupees, not paise)
    currency: str = "INR"
    start: date
    end: date
    daily_cap: Optional[float] = None
    flexibility_pct: float = 0
    notes: str = ""                 # e.g. "includes GST", raw proposal text excerpt


class Deliverables(BaseModel):
    n_images: int
    n_videos: int
    ratios: list[Ratio]
    video_lengths_s: list[int] = [15]
    platforms: list[str] = ["facebook", "instagram"]


class ClientBrief(BaseModel):
    client_id: str
    business_name: str
    website: Optional[str] = None
    industry: str
    products: list[Product]
    goal: Goal
    kpi_targets: KPITargets = KPITargets()
    geo: list[str]                  # ISO country codes, e.g. ["IN"]
    languages: list[str] = ["en"]
    audience_notes: str = ""
    brand: BrandKit = BrandKit()
    budget: BudgetInput
    deliverables: Deliverables
    special_ad_category: SpecialAdCategory = "none"
    landing_pages: list[str] = []
    constraints: list[str] = []


# ---------- Research (Malav: M2) ----------

class Persona(BaseModel):
    ref: str
    name: str
    description: str
    age_range: tuple[int, int] = (18, 65)
    genders: list[Literal["male", "female", "all"]] = ["all"]
    interests: list[str] = []       # plain words; Shlok resolves them to Meta interest IDs
    pains: list[str] = []
    desires: list[str] = []
    phrases: list[str] = []         # verbatim customer language
    objections: list[str] = []
    evidence: list[str] = []        # source URLs


class Competitor(BaseModel):
    name: str
    url: Optional[str] = None
    ad_notes: list[str] = []
    angles: list[str] = []


class ResearchReport(BaseModel):
    client_id: str
    summary: str
    product_insights: list[str] = []
    personas: list[Persona]
    competitors: list[Competitor] = []
    trends: list[str] = []
    recommended_angles: list[str] = []


# ---------- Blueprint (Malav plans structure; Shlok fills budget + targeting) ----------

class AdPlan(BaseModel):
    ref: str
    hook_id: str
    format: Literal["image", "video", "carousel"]
    ratios: list[Ratio]


class AdSetPlan(BaseModel):
    ref: str
    name: str
    persona_ref: str
    angle: str
    rationale: str = ""
    ads: list[AdPlan]


class CampaignPlan(BaseModel):
    ref: str
    name: str
    objective: Objective
    funnel_stage: FunnelStage = "TOF"
    rationale: str = ""
    adsets: list[AdSetPlan]


class CampaignBlueprint(BaseModel):
    client_id: str
    campaigns: list[CampaignPlan]
    rationale: str = ""

    def adsets(self) -> list[AdSetPlan]:
        return [a for c in self.campaigns for a in c.adsets]


class Hook(BaseModel):
    id: str
    adset_ref: str
    text: str
    framework: str = ""             # PAS, social proof, curiosity...
    critic_score: Optional[float] = None


# ---------- Creatives (Malav: M6-M8 -> Shlok) ----------

class AdCopy(BaseModel):
    primary_text: str               # aim <=125 chars visible
    headline: str                   # aim <=40
    description: str = ""           # aim <=30
    cta: CTA = "LEARN_MORE"


class QAResult(BaseModel):
    score: float = 0                # 0-100
    passed: bool = False
    checks: dict[str, bool] = {}
    notes: str = ""


class Creative(BaseModel):
    creative_id: str
    campaign_ref: str
    adset_ref: str
    ad_ref: str
    hook_id: str
    kind: Literal["image", "video"]
    ratio: Ratio
    width: int
    height: int
    duration_s: Optional[float] = None
    path: str                       # local file path (media/...) — Shlok uploads it to Meta
    thumbnail_path: Optional[str] = None  # required for video: Meta needs a thumbnail image
    ad_copy: AdCopy
    landing_url: str                # includes UTMs
    model_used: str = ""
    template_id: Optional[str] = None
    prompt: str = ""
    qa: QAResult = QAResult()
    version: int = 1
    parent_id: Optional[str] = None


class CreativePackage(BaseModel):
    client_id: str
    blueprint: CampaignBlueprint
    creatives: list[Creative]

    def for_ad(self, ad_ref: str) -> list[Creative]:
        return [c for c in self.creatives if c.ad_ref == ad_ref]


# ---------- Budget / targeting / launch (Shlok) ----------

class FeasibilityResult(BaseModel):
    feasible: bool
    n_adsets_requested: int
    max_adsets: int
    min_daily_per_adset: float
    total_daily: float
    suggestion: str


class BudgetPlan(BaseModel):
    client_id: str
    currency: str
    total: float
    days: int
    mode: Literal["ABO", "CBO"]
    bid_strategy: Literal["LOWEST_COST_WITHOUT_CAP", "COST_CAP", "LOWEST_COST_WITH_MIN_ROAS"]
    testing_reserve: float          # DAILY amount held back for new creatives from the feedback loop
    campaign_daily: dict[str, float]   # campaign_ref -> daily budget (major units)
    adset_daily: dict[str, float]      # adset_ref -> daily budget (major units)
    rationale: str = ""


class TargetingSpec(BaseModel):
    adset_ref: str
    spec: dict                      # Meta targeting JSON
    summary: str                    # plain English for the approval card


class MetaObject(BaseModel):
    local_ref: str
    kind: Literal["image", "video", "campaign", "adset", "creative", "ad"]
    meta_id: str
    status: str = "PAUSED"


class LaunchResult(BaseModel):
    dry_run: bool
    objects: list[MetaObject] = []
    payloads: list[dict] = []       # exact API payloads (dry-run + audit trail)


class InsightRow(BaseModel):
    ts: datetime
    ad_ref: str
    adset_ref: str
    spend: float = 0
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    revenue: float = 0
    video_3s: int = 0
    thruplays: int = 0
    frequency: float = 0
    source: Literal["meta", "simulated"] = "meta"


class CreativeFeedback(BaseModel):
    """Shlok S7 -> Malav M4: what is working live and what to make next."""
    client_id: str
    adset_ref: str
    winners: list[dict] = []        # {creative_id, hook, metric: value...}
    losers: list[dict] = []         # {creative_id, reason}
    insight: str
    n_new: int = 3
    formats: list[str] = ["image 4:5"]
    direction: str


# ---------- Audit (shared) ----------

Owner = Literal["malav", "shlok", "human"]


class Finding(BaseModel):
    severity: Literal["blocker", "major", "minor"]
    dimension: str                  # brief, strategy, creative, copy_policy, budget, targeting, technical
    object_ref: str
    owner: Owner
    message: str


class FixPrompt(BaseModel):
    owner: Owner
    object_refs: list[str]
    prompt: str


class AuditReport(BaseModel):
    decision: Literal["PASS", "PASS_WITH_NOTES", "FAIL"]
    scores: dict[str, int]
    findings: list[Finding] = []
    fix_prompts: list[FixPrompt] = []


# ---------- Agent bus + approvals ----------

class AgentMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    thread_id: str
    client_id: str
    sender: str                     # "malav.strategy", "shlok.budget", "auditor", "human"
    recipient: str
    type: Literal["REQUEST", "RESPONSE", "INFORM", "QUESTION", "FEEDBACK", "FIX_PROMPT", "LOG"]
    payload: dict = {}
    created_at: datetime = Field(default_factory=now)


class GateDecision(BaseModel):
    action: Literal["approve", "changes", "reject"]
    comment: str = ""
