"""Typed shapes the LLM must return (enforced with structured outputs)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "digital_products",       # templates, prompt packs, ebooks, printables
    "freelance_services",     # writing, design, editing, VA work delivered with AI help
    "automation_agency",      # chatbots / workflows built for small businesses
    "content_channel",        # faceless YouTube/TikTok/newsletter monetised by ads or sponsors
    "affiliate_marketing",
    "micro_saas",             # small paid tools / GPTs / apps
    "print_on_demand",
    "stock_media",            # AI images/music/video licensed on marketplaces
    "education_coaching",     # tutoring, courses, workshops
    "local_business_services",
    "arbitrage_reselling",
    "other",
]

Policy = Literal[
    "scam_or_mlm",
    "fake_reviews_or_engagement",
    "spam_or_unsolicited_bulk_messaging",
    "platform_tos_violation",
    "copyright_or_trademark_infringement",
    "impersonation_or_deepfake",
    "academic_dishonesty",
    "unlicensed_financial_or_medical_or_legal_advice",
    "gambling_or_trading_signals",
    "deceptive_undisclosed_ai",
    "requires_upfront_money",
    "privacy_or_personal_data_misuse",
    "adult_content",
    "excluded_by_user",
]


class ExtractedIdea(BaseModel):
    title: str = Field(description="Short neutral name of the money-making method, e.g. 'Sell Notion budget templates on Etsy'")
    summary: str = Field(description="2-3 sentences: what you sell, to whom, through which channel")
    category: Category
    steps: list[str] = Field(description="Concrete steps the reel describes, in order")
    tools: list[str] = Field(description="Tools/platforms mentioned (ChatGPT, Canva, Etsy, Fiverr...)")
    claimed_monthly_usd: float | None = Field(description="Income per month the reel claims, converted to USD/month; null if none claimed")
    claimed_upfront_usd: float | None = Field(description="Money the reel says you need to start; 0 if it says free; null if unknown")
    red_flags: list[str] = Field(description="Hype or scam signals: 'link in bio' course upsell, guaranteed income, no proof, etc.")
    hype: int = Field(description="0 = sober and specific, 10 = pure clickbait")


class ExtractionResult(BaseModel):
    is_relevant: bool = Field(description="True only if the reel actually describes a way to earn money")
    ideas: list[ExtractedIdea]


class ComplianceVerdict(BaseModel):
    verdict: Literal["allow", "caution", "reject"]
    violated_policies: list[Policy]
    reasons: list[str]
    upfront_cost_usd: float = Field(description="Realistic minimum cash needed to start, using free tiers where possible")
    safe_variant: str = Field(description="If the idea can be done legitimately, how; empty string if not")


class Range(BaseModel):
    low: float = Field(description="Pessimistic (10th percentile)")
    mode: float = Field(description="Most likely")
    high: float = Field(description="Optimistic (90th percentile)")


class EconomicModel(BaseModel):
    revenue_model: str = Field(description="How money arrives, e.g. 'one-off $9 template sales on Etsy'")
    recurring: bool = Field(description="True when one customer pays repeatedly (retainer, subscription)")
    monthly_churn: float = Field(description="Fraction of recurring customers lost per month (0 if not recurring)")
    hours_to_launch: Range
    weekly_hours: Range = Field(description="Ongoing hours per week once launched")
    weekly_reach_at_maturity: Range = Field(description="People per week who see the offer after ramp-up")
    conversion_rate: Range = Field(description="Fraction of reached people who pay (0-1)")
    price_usd: Range = Field(description="Revenue per sale or per month for recurring")
    ramp_weeks: Range = Field(description="Weeks from launch until reach is at maturity")
    p_no_traction: float = Field(description="Probability (0-1) the attempt never gets meaningful reach at all")
    p_shutdown_per_month: float = Field(description="Monthly probability (0-1) of account ban / policy change killing it")
    fee_pct: float = Field(description="Platform + payment fees as a fraction of revenue (0-1)")
    upfront_cost_usd: float
    evidence_quality: int = Field(description="0-10: how solid the real-world evidence found is (10 = verified public revenue data)")
    saturation: int = Field(description="0-10: how crowded the market is (10 = extremely saturated)")
    automation_potential: int = Field(description="0-10: share of the work an AI agent could do unattended")
    skill_fit: int = Field(description="0-10: fit with the operator profile")
    real_world_evidence: list[str] = Field(description="Specific findings with numbers, each with its source")
    key_risks: list[str]
    verdict_summary: str


class Criterion(BaseModel):
    metric: str = Field(description="snake_case metric the operator logs, e.g. revenue_usd, sales, visits, replies, signups")
    op: Literal[">=", "<="]
    threshold: float


class PlanTask(BaseModel):
    title: str
    detail: str = Field(description="Exactly what to do, specific enough to execute without further questions")
    kind: Literal["agent_asset", "human_action"] = Field(
        description="agent_asset: the AI produces a file (copy, template, script, listing text). "
        "human_action: anything that touches the outside world (create account, publish, message someone, accept payment)"
    )
    deliverable: str = Field(description="What exists when the task is done")


class ExperimentPlan(BaseModel):
    hypothesis: str
    offer: str = Field(description="The exact thing being sold and its price")
    channel: str
    duration_days: int
    success_criteria: list[Criterion]
    kill_criteria: list[Criterion]
    tasks: list[PlanTask]
    metrics_to_log: list[str]
    disclosure_note: str = Field(description="How AI use and any affiliate links are disclosed to buyers")


class AssetFile(BaseModel):
    filename: str = Field(description="Relative file name with extension, e.g. listing.md or product/prompts.csv")
    content: str


class AssetBundle(BaseModel):
    files: list[AssetFile]
    notes: str = Field(description="What the operator should check or change before using these files")
