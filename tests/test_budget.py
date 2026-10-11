from datetime import date, timedelta

import pytest

from mosaico import budget
from mosaico.db import now
from mosaico.scheduler import Agent
from mosaico.sources import save_reels
from mosaico.sources.seed import sample_reels


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def started(db, days_ago: int) -> None:
    db.set_kv("started_on", day(-days_ago))


def spend(db, amount: float, on: str) -> None:
    db.insert("llm_calls", {"ts": now(), "day": on, "purpose": "t", "model": "m", "input_tokens": 0,
                            "output_tokens": 0, "cache_read": 0, "cache_write": 0, "web_searches": 0, "cost_usd": amount})


def test_discovery_is_paced_and_capped(cfg, db):
    s = budget.status(cfg, db)
    assert s.phase == budget.DISCOVERY and s.day == 1
    assert s.allowance == pytest.approx(12 / 7)

    spend(db, 1.0, day(0))
    assert budget.status(cfg, db).allowance == pytest.approx(12 / 7 - 1.0)

    # A big spend on an earlier day reduces later daily quotas, never raises the total.
    started(db, 3)
    db.execute("DELETE FROM llm_calls")
    spend(db, 9.0, day(-2))
    s = budget.status(cfg, db)
    assert s.allowance == pytest.approx(3.0 / 4)

    spend(db, 3.0, day(-1))  # total discovery spend = 12 → phase is over early
    assert budget.status(cfg, db).phase == budget.MAINTENANCE


def test_maintenance_limits(cfg, db):
    started(db, 10)
    spend(db, 11.0, day(-8))
    s = budget.status(cfg, db)
    assert s.phase == budget.MAINTENANCE and s.allowance == pytest.approx(0.30)
    for i in range(1, 11):
        spend(db, 0.30, day(-i + 1) if i == 1 else day(-i))
    # $3/month cap reached even though today's spend is within the daily limit.
    assert budget.status(cfg, db).allowance == 0


def test_breakeven_pause_and_resume(cfg, db):
    started(db, 7 + 31)
    spend(db, 12.0, day(-37))
    assert budget.check_breakeven(cfg, db) is not None
    s = budget.status(cfg, db)
    assert s.phase == budget.PAUSED and s.allowance == 0

    budget.resume(db)
    assert budget.status(cfg, db).phase == budget.MAINTENANCE
    assert budget.check_breakeven(cfg, db) is None  # grace period after a manual resume


def test_breakeven_keeps_running_when_profitable(cfg, db):
    started(db, 7 + 31)
    spend(db, 12.0, day(-37))
    db.insert("opportunities", {"slug": "x", "title": "x", "created_at": now(), "updated_at": now()})
    db.insert("outcomes", {"opportunity_id": 1, "day": day(-1), "revenue_usd": 40.0})
    assert budget.check_breakeven(cfg, db) is None


def test_maintenance_skips_research_stages(cfg, db, llm):
    save_reels(db, sample_reels()[:2])
    started(db, 10)
    Agent(cfg, db, llm).tick()
    assert not [c for c in llm.calls if c[0].startswith(("extract", "research", "compliance"))]
    assert db.scalar("SELECT COUNT(*) FROM reels WHERE status = 'new'") == 2


def test_guard_uses_worst_case(cfg, db):
    from tests.test_units import FakeMessages
    from types import SimpleNamespace

    from mosaico.llm import BudgetExceeded, ClaudeLLM
    from mosaico.models import ExtractionResult

    started(db, 10)  # maintenance: $0.30/day
    llm = ClaudeLLM(cfg, db, client=SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages())))
    llm.structured("small", "plan", "s", "p", ExtractionResult, max_tokens=8000)   # ≤ $0.19 worst case
    with pytest.raises(BudgetExceeded):
        llm.structured("big", "plan", "s", "p", ExtractionResult, max_tokens=16000)  # $0.34 > $0.30


def test_early_discovery_end_counts_toward_maintenance(cfg, db):
    started(db, 3)
    spend(db, 12.0, day(-1))
    assert budget.status(cfg, db).phase == budget.MAINTENANCE
    assert db.get_kv("discovery_ended_on") == day(0)
    spend(db, 0.30, day(0))
    assert budget.status(cfg, db).allowance == 0  # today's maintenance spend counts
