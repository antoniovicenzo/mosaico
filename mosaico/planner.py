"""Design small, cheap experiments for the top-scoring opportunities."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import DB, dumps, loads, now
from .llm import LLM
from .models import ExperimentPlan
from .tracker import totals

SYSTEM = """You design the smallest experiment that can prove or disprove a side-income method within a few \
weeks, for one operator with no cash to invest. You are given the research memo, an economic model, and \
Monte Carlo results.

Rules:
- Goal: the first real dollar from a real customer as fast as possible, or a clear signal to stop.
- Zero cash: free tiers only. Never require ads, paid tools or inventory.
- Lawful and platform-compliant: no fake reviews or engagement, no ban evasion, no bulk unsolicited messages. \
Outreach must be personalised, low-volume and respect anti-spam law (GDPR/ePrivacy; in Spain, LSSI requires \
prior consent for commercial email, so prefer contact forms, marketplaces, in-person, or public channels). \
Disclose AI use and affiliate links where platforms or law require it.
- Split work into tasks. agent_asset tasks are files an AI can fully produce (product content, listing copy, \
templates, scripts, pitch drafts, content calendars, price research tables). human_action tasks are \
everything that touches the outside world (creating accounts, publishing, contacting people, payments, \
anything needing identity/KYC). Put agent_asset tasks first so the operator gets ready-made materials.
- success_criteria and kill_criteria use metrics the operator can log easily (revenue_usd, sales, visits, \
replies, signups, hours). Make them concrete and achievable to measure within duration_days (7-28).
- Fit the operator's available hours."""


def active_count(db: DB) -> int:
    return db.scalar("SELECT COUNT(*) FROM experiments WHERE status = 'running'") or 0


def candidates(cfg: Config, db: DB) -> list:
    return db.all(
        "SELECT * FROM opportunities WHERE status IN ('researched', 'scaling') AND score >= ? "
        "AND id NOT IN (SELECT opportunity_id FROM experiments WHERE status = 'running') "
        "ORDER BY status = 'scaling' DESC, score DESC",
        (cfg.strategy.min_score_to_plan,),
    )


def plan_experiment(cfg: Config, db: DB, llm: LLM, opp_id: int) -> int:
    opp = db.one("SELECT * FROM opportunities WHERE id = ?", (opp_id,))
    sim = loads(opp["sim_json"], {})
    sim_brief = {k: sim.get(k) for k in (
        "p_any_revenue", "p_over_100", "revenue_median", "revenue_p90", "hourly_mean",
        "days_to_first_dollar_median", "monthly_run_rate_median",
    )}
    history = ""
    if opp["status"] == "scaling":
        prev = db.all("SELECT * FROM experiments WHERE opportunity_id = ? ORDER BY id", (opp_id,))
        lines = []
        for e in prev:
            lines.append(f"- Experiment #{e['id']} ({e['status']}): {e['decision_note']}; totals {totals(db, e['id'])}")
        history = (
            "\nThis method already passed a validation experiment. Design the NEXT stage: scale what worked "
            "(more listings, higher price, retainer offers, systematise), still with zero cash.\n" + "\n".join(lines)
        )
    p = cfg.profile
    prompt = (
        f"Operator: {p.hours_per_week} h/week, skills {', '.join(p.skills)}, languages {', '.join(p.languages)}, "
        f"country {p.country}.\n"
        f"Method: {opp['title']} ({opp['category']})\n{opp['summary']}\n"
        f"Compliance: {opp['compliance_json']}\n"
        f"Economic model: {opp['model_json']}\n"
        f"Simulation (90 days): {json.dumps(sim_brief)}\n{history}\n"
        f"<memo>\n{opp['research_memo'] or ''}\n</memo>"
    )
    plan = llm.structured(
        purpose=f"plan:{opp_id}", role="plan", system=SYSTEM, prompt=prompt, schema=ExperimentPlan, max_tokens=12000,
    )
    return create_experiment(db, opp_id, plan)


def create_experiment(db: DB, opp_id: int, plan: ExperimentPlan) -> int:
    started = datetime.now(timezone.utc)
    duration = min(max(plan.duration_days, 7), 28)
    exp_id = db.insert("experiments", {
        "opportunity_id": opp_id,
        "status": "running",
        "plan_json": plan.model_dump_json(),
        "started_at": started.isoformat(timespec="seconds"),
        "deadline": (started + timedelta(days=duration)).isoformat(timespec="seconds"),
    })
    for i, task in enumerate(plan.tasks):
        db.insert("tasks", {
            "experiment_id": exp_id,
            "idx": i,
            "title": task.title,
            "detail": dumps({"detail": task.detail, "deliverable": task.deliverable}),
            "kind": "agent" if task.kind == "agent_asset" else "human",
            "status": "todo",
            "updated_at": now(),
        })
    db.update("opportunities", opp_id, {"status": "active", "updated_at": now()})
    db.log("plan", f"Started experiment #{exp_id} for opportunity #{opp_id}: {plan.hypothesis}")
    return exp_id
