from datetime import datetime, timedelta, timezone

import pytest

from mosaico import executor, extract, planner, report, research, tracker
from mosaico.llm import ItemError
from mosaico.scheduler import Agent
from mosaico.sources import save_reels
from mosaico.sources.seed import sample_reels


def test_end_to_end(cfg, db, llm):
    assert save_reels(db, sample_reels()) == len(sample_reels())
    assert save_reels(db, sample_reels()) == 0  # idempotent

    extract.extract_pending(db, llm, limit=100)
    assert db.scalar("SELECT COUNT(*) FROM reels WHERE status = 'new'") == 0
    n_opps = db.scalar("SELECT COUNT(*) FROM opportunities")
    assert 0 < n_opps <= len(sample_reels())

    # The rule layer must reject the obviously bad samples without spending an LLM call.
    for opp in db.all("SELECT id FROM opportunities"):
        research.research_opportunity(cfg, db, llm, opp["id"])
    rejected = {r["title"] for r in db.all("SELECT title FROM opportunities WHERE status = 'rejected'")}
    assert {"Fake reviews", "Essay writing for students", "Trading bot"} <= rejected
    assert "Sell Notion templates" not in rejected
    researched = db.all("SELECT * FROM opportunities WHERE status = 'researched'")
    assert researched and all(r["score"] is not None for r in researched)

    exp_id = planner.plan_experiment(cfg, db, llm, researched[0]["id"])
    task = executor.next_task(db)
    out = executor.run_task(cfg, db, llm, task)
    assert (out / "listing.md").exists()
    assert not (cfg.base_dir / "escape.txt").exists()
    assert not any(p.name == "escape.txt" and ".." in str(p) for p in out.rglob("*"))
    assert len(report.human_tasks(db)) == 1

    tracker.log_outcome(db, researched[0]["id"], revenue=9, hours=3, metrics={"visits": 80})
    assert tracker.evaluate_experiments(db) == [(exp_id, "succeeded")]
    assert db.scalar("SELECT status FROM opportunities WHERE id = ?", (researched[0]["id"],)) == "scaling"
    assert tracker.calibration_for(db, researched[0]["category"]) != 1.0

    html = report.render_html(db)
    assert "Ranked opportunities" in html and "Fake reviews" in html


def test_deadline_kills_experiment(cfg, db, llm):
    save_reels(db, sample_reels()[:1])
    extract.extract_pending(db, llm)
    opp_id = db.scalar("SELECT id FROM opportunities")
    research.research_opportunity(cfg, db, llm, opp_id)
    exp_id = planner.plan_experiment(cfg, db, llm, opp_id)
    ago = lambda d: (datetime.now(timezone.utc) - timedelta(days=d)).isoformat(timespec="seconds")  # noqa: E731
    db.update("experiments", exp_id, {"started_at": ago(60), "deadline": ago(1)})
    assert tracker.evaluate_experiments(db) == [(exp_id, "killed")]
    assert tracker.calibration_for(db, "digital_products") < 1.0


def test_agent_tick_runs_all_stages(cfg, db, llm):
    save_reels(db, sample_reels()[:3])
    db.set_kv("started_on", (datetime.now(timezone.utc) - timedelta(days=4)).date().isoformat())
    agent = Agent(cfg, db, llm)
    for _ in range(6):
        agent.tick()
    assert db.scalar("SELECT COUNT(*) FROM experiments") >= 1
    assert (cfg.base_dir / "data" / "report.html").exists()
    errors = db.all("SELECT * FROM events WHERE level = 'error'")
    assert not errors, [dict(e) for e in errors]


def test_failing_item_is_parked(cfg, db, llm):
    save_reels(db, sample_reels()[:1])
    extract.extract_pending(db, llm)

    def outage(*a, **k):
        raise ConnectionError("network down")

    llm.research = outage
    agent = Agent(cfg, db, llm)
    agent.tick()  # a system failure is logged but the item stays queued
    assert db.scalar("SELECT status FROM opportunities") == "candidate"

    def bad_output(*a, **k):
        raise ItemError("bad output")

    llm.research = bad_output
    agent.research()
    assert db.scalar("SELECT status FROM opportunities") == "error"


def test_system_errors_do_not_park_reels(cfg, db, llm):
    save_reels(db, sample_reels()[:2])

    def no_auth(*a, **k):
        raise TypeError("Could not resolve authentication method")

    llm.structured = no_auth
    with pytest.raises(TypeError):
        extract.extract_pending(db, llm)
    assert db.scalar("SELECT COUNT(*) FROM reels WHERE status = 'new'") == 2
