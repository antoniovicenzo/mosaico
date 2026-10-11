from types import SimpleNamespace

import pytest

from mosaico.compliance import rule_check
from mosaico.executor import safe_relpath
from mosaico.extract import similarity
from mosaico.llm import BudgetExceeded, ClaudeLLM, price_call
from mosaico.models import ExtractionResult, Range
from mosaico.simulate import simulate
from mosaico.sources.apify import parse_item
from mosaico.sources.inbox import reel_id

from .conftest import econ


def test_simulation_is_deterministic_and_monotonic():
    a = simulate(econ(), hours_per_week=10, runs=500, seed=1)
    b = simulate(econ(), hours_per_week=10, runs=500, seed=1)
    assert a == b
    better = simulate(econ(conversion_rate=Range(low=0.02, mode=0.05, high=0.08)), hours_per_week=10, runs=500, seed=1)
    assert better.revenue_mean > a.revenue_mean
    dead = simulate(econ(p_no_traction=1.0), hours_per_week=10, runs=500, seed=1)
    assert dead.revenue_mean < a.revenue_mean
    assert 0 <= a.p_any_revenue <= 1 and a.revenue_p10 <= a.revenue_median <= a.revenue_p90


def test_recurring_revenue_accumulates():
    one_off = simulate(econ(price_usd=Range(low=100, mode=100, high=100)), 10, runs=300, seed=2)
    recurring = simulate(econ(price_usd=Range(low=100, mode=100, high=100), recurring=True, monthly_churn=0.1), 10, runs=300, seed=2)
    assert recurring.monthly_run_rate_median >= one_off.monthly_run_rate_median


def test_calibration_scales_outcomes():
    base = simulate(econ(), 10, runs=500, seed=3)
    low = simulate(econ(), 10, runs=500, seed=3, calibration=0.2)
    assert low.revenue_mean < base.revenue_mean


@pytest.mark.parametrize("text,policy", [
    ("Write 100 fake reviews with ChatGPT", "fake_reviews_or_engagement"),
    ("Run it through a humanizer so Turnitin can't detect it", "academic_dishonesty"),
    ("Join my trading signals group", "gambling_or_trading_signals"),
    ("Use multiple accounts so you don't get banned", "platform_tos_violation"),
    ("send 500 DMs a day", "spam_or_unsolicited_bulk_messaging"),
])
def test_rules_catch_bad_ideas(text, policy):
    assert policy in rule_check(text)


def test_rules_allow_normal_ideas():
    assert rule_check("Sell Notion budget templates on Etsy for $9") == []
    assert rule_check("Offer AI chatbot setup to local dentists") == []
    assert rule_check("Dropshipping store", ["dropshipping"]) == ["excluded_by_user"]


def test_similarity():
    assert similarity("Sell Notion templates on Etsy", "Selling Notion templates on Gumroad") > 0.4
    assert similarity("Sell Notion templates", "Faceless YouTube channel") == 0


def test_safe_relpath():
    assert str(safe_relpath("a/b.md")) == "a/b.md"
    assert safe_relpath("../x") is None
    assert str(safe_relpath("/etc/passwd")) == "etc/passwd"
    assert safe_relpath("") is None


def test_apify_parse_and_inbox_ids():
    reel = parse_item({"shortCode": "ABC", "type": "Video", "caption": "hi", "videoPlayCount": 10, "ownerUsername": "u"})
    assert reel.id == "ig:ABC" and reel.views == 10 and reel.author == "u"
    assert parse_item({"shortCode": "X", "type": "Image"}) is None
    assert reel_id("https://www.instagram.com/reel/ABC/?igsh=1") == "ig:ABC"


def test_price_call():
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=10))
    assert price_call("claude-opus-5-5", usage) == pytest.approx(4.0 + 0.10)


class FakeMessages:
    def __init__(self):
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            model=kwargs["model"], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0, server_tool_use=None),
            parsed_output=ExtractionResult(is_relevant=False, ideas=[]),
        )


def test_claude_llm_records_cost_and_enforces_budget(cfg, db):
    msgs = FakeMessages()
    client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    llm = ClaudeLLM(cfg, db, client=client)
    out = llm.structured("t", "plan", "sys", "hi", ExtractionResult)
    assert out.is_relevant is False
    assert msgs.kwargs["model"] == "claude-opus-5-5"
    assert msgs.kwargs["fallbacks"] == "default" and msgs.kwargs["output_config"] == {"effort": "medium"}
    assert llm.spent_today() == pytest.approx((1000 * 4 + 500 * 20) / 1e6)

    llm.structured("t", "extract", "sys", "hi", ExtractionResult)
    assert "fallbacks" not in msgs.kwargs and msgs.kwargs["model"] == "claude-haiku-5-5"

    cfg.budget.daily_usd = 0.01
    with pytest.raises(BudgetExceeded):
        llm.structured("t", "plan", "sys", "hi", ExtractionResult)


def test_example_config_loads():
    from pathlib import Path

    from mosaico.config import load_config

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.example.toml")
    assert cfg.budget.discovery_usd + cfg.budget.maintenance_monthly_usd == 15 and cfg.strategy.weights["ev_per_hour"] == 0.30
