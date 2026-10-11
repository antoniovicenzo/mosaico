from __future__ import annotations

import re

import pytest

from mosaico.config import Config
from mosaico.db import DB
from mosaico.llm import ResearchResult
from mosaico.models import (
    AssetBundle, AssetFile, ComplianceVerdict, Criterion, EconomicModel, ExperimentPlan, ExtractedIdea,
    ExtractionResult, PlanTask, Range,
)


def econ(**over) -> EconomicModel:
    base = dict(
        revenue_model="one-off sales", recurring=False, monthly_churn=0.0,
        hours_to_launch=Range(low=5, mode=10, high=20), weekly_hours=Range(low=2, mode=4, high=6),
        weekly_reach_at_maturity=Range(low=50, mode=200, high=600), conversion_rate=Range(low=0.005, mode=0.015, high=0.03),
        price_usd=Range(low=7, mode=12, high=19), ramp_weeks=Range(low=2, mode=4, high=8),
        p_no_traction=0.4, p_shutdown_per_month=0.01, fee_pct=0.1, upfront_cost_usd=0,
        evidence_quality=6, saturation=7, automation_potential=7, skill_fit=7,
        real_world_evidence=["x (source)"], key_risks=["saturation"], verdict_summary="ok",
    )
    base.update(over)
    return EconomicModel(**base)


class FakeLLM:
    """Deterministic stand-in for Claude used by the tests."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def remaining_budget(self) -> float:
        return 100.0

    def structured(self, purpose, role, system, prompt, schema, max_tokens=8000):
        self.calls.append((purpose, schema.__name__))
        if schema is ExtractionResult:
            m = re.search(r"\[SAMPLE: ([^\]]+)\]", prompt)
            title = m.group(1) if m else "Generic idea"
            return ExtractionResult(is_relevant=True, ideas=[ExtractedIdea(
                title=title, summary=f"{title} summary", category="digital_products", steps=["a", "b"],
                tools=["ChatGPT"], claimed_monthly_usd=3000, claimed_upfront_usd=0, red_flags=[], hype=6,
            )])
        if schema is ComplianceVerdict:
            return ComplianceVerdict(verdict="allow", violated_policies=[], reasons=[], upfront_cost_usd=0, safe_variant="")
        if schema is EconomicModel:
            return econ()
        if schema is ExperimentPlan:
            return ExperimentPlan(
                hypothesis="People buy it", offer="Template, $9", channel="Etsy", duration_days=14,
                success_criteria=[Criterion(metric="revenue_usd", op=">=", threshold=9)],
                kill_criteria=[Criterion(metric="visits", op="<=", threshold=50)],
                tasks=[
                    PlanTask(title="Write listing", detail="Write it", kind="agent_asset", deliverable="listing.md"),
                    PlanTask(title="Publish listing", detail="Publish it", kind="human_action", deliverable="live URL"),
                ],
                metrics_to_log=["visits", "sales"], disclosure_note="Made with AI assistance",
            )
        if schema is AssetBundle:
            return AssetBundle(files=[
                AssetFile(filename="listing.md", content="# Listing"),
                AssetFile(filename="../../escape.txt", content="nope"),
            ], notes="check")
        raise AssertionError(f"unexpected schema {schema}")

    def research(self, purpose, system, prompt, max_searches=8):
        self.calls.append((purpose, "research"))
        return ResearchResult(text="Memo with evidence.", sources=[{"url": "https://example.com", "title": "Ex"}])


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config(base_dir=tmp_path)
    c.strategy.simulations = 400
    c.strategy.min_score_to_plan = 0
    c.sources.transcribe = False
    return c


@pytest.fixture
def db(cfg) -> DB:
    d = DB(cfg.db_path)
    yield d
    d.close()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()
