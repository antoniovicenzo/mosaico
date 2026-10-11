"""Backtest an opportunity: compliance → web research → economic model → simulation → score."""

from __future__ import annotations

import json
import logging
import math

from . import compliance
from .config import Config
from .db import DB, dumps, now, today
from .llm import LLM
from .models import EconomicModel
from .scoring import hype_ratio, score
from .simulate import simulate
from .tracker import calibration_for

log = logging.getLogger(__name__)

RESEARCH_SYSTEM = """You are a skeptical market analyst. Social media reels about "making money with AI" suffer \
from extreme survivorship bias and are often funnels for selling courses. Your job is to find out what \
actually happens to a typical beginner who tries the method today.

Search the web for independent evidence, preferring sources that are not selling the method:
- Real earnings reports with numbers (Reddit, Indie Hackers, forums, case studies, marketplace sales counters, \
public dashboards). Note the sample: one success story is not a base rate.
- Market size and saturation (e.g. number of competing listings/gigs/channels, how prices have moved).
- Typical prices, conversion rates, and traffic a beginner can realistically get with no ad budget.
- Platform rules that matter (AI-content disclosure, upload limits, monetisation policies, anti-spam law).
- Time to first sale for beginners.
- Free tools/tiers that make a zero-cash start possible, and any hidden costs.

Write a concise research memo (500-900 words) with sections: Evidence, Market & saturation, Unit economics \
(give low / likely / high numbers), Policy & legal, Hidden costs, Bottom line for a beginner. Cite sources inline. \
State plainly when evidence is thin. Never invent numbers; when you estimate, say so."""

MODEL_SYSTEM = """Convert the research memo into a quantitative economic model of ONE beginner attempting the \
method with the operator's constraints. Use ranges that reflect real uncertainty: low = pessimistic 10th \
percentile, high = optimistic 90th percentile. Calibrate to the memo's evidence, not to the reels' claims.

Definitions:
- weekly_reach_at_maturity: people per week who actually see the offer (listing views, profile visits, \
prospects contacted with consent, etc.) after the ramp period.
- conversion_rate: fraction of those people who pay (0-1). Marketplace listings typically convert 0.5-3%; \
warm personalised B2B outreach replies 2-10% and closes a fraction of those.
- price_usd: revenue per sale, or per customer per month when recurring=true.
- p_no_traction: probability the attempt never gets meaningful reach (algorithm ignores it, no buyers).
- Scores (evidence_quality, saturation, automation_potential, skill_fit) are integers 0-10."""


def priority(row) -> float:
    return row["mentions"] * math.log10((row["total_views"] or 0) + 10)


def due_for_research(cfg: Config, db: DB) -> list:
    done_today = db.scalar(
        "SELECT COUNT(*) FROM opportunities WHERE substr(researched_at, 1, 10) = ?", (today(),)
    ) or 0
    slots = max(0, cfg.strategy.research_per_day - done_today)
    if not slots:
        return []
    rows = db.all(
        "SELECT * FROM opportunities WHERE status = 'candidate' AND mentions >= ?",
        (cfg.strategy.min_mentions,),
    )
    return sorted(rows, key=priority, reverse=True)[:slots]


def research_opportunity(cfg: Config, db: DB, llm: LLM, opp_id: int) -> dict:
    # Reuse an earlier verdict (e.g. when the budget ran out mid-research yesterday).
    verdict = compliance.stored_verdict(db.one("SELECT * FROM opportunities WHERE id = ?", (opp_id,)))
    if verdict is None:
        verdict = compliance.review(cfg, db, llm, opp_id)
    if verdict.verdict == "reject":
        return {"status": "rejected", "reasons": verdict.reasons}

    opp = db.one("SELECT * FROM opportunities WHERE id = ?", (opp_id,))
    p = cfg.profile
    profile = (
        f"Operator: {p.hours_per_week} h/week, skills={', '.join(p.skills)}, languages={', '.join(p.languages)}, "
        f"based in {p.country}, max upfront cash ${p.max_upfront_usd:.0f}."
    )
    claimed = f"${opp['claimed_monthly_usd']:.0f}/month" if opp["claimed_monthly_usd"] else "no specific figure"
    safe = f"\nCompliance notes: {'; '.join(verdict.reasons)}\nSafe variant to evaluate: {verdict.safe_variant}" if verdict.verdict == "caution" else ""
    brief = (
        f"Method: {opp['title']}\nDescription: {opp['summary']}\nCategory: {opp['category']}\n"
        f"Seen in {opp['mentions']} reels with {opp['total_views']:,} total views; reels claim {claimed}.{safe}\n{profile}\n"
        f"Today is {today()}."
    )
    memo = llm.research(f"research:{opp_id}", RESEARCH_SYSTEM, brief, max_searches=8)
    model = llm.structured(
        purpose=f"model:{opp_id}",
        role="research",
        system=MODEL_SYSTEM,
        prompt=f"{brief}\n\n<memo>\n{memo.text}\n</memo>",
        schema=EconomicModel,
        max_tokens=8000,
    )
    return apply_model(cfg, db, opp_id, model, memo.text, memo.sources)


def apply_model(cfg: Config, db: DB, opp_id: int, model: EconomicModel, memo: str = "", sources: list | None = None) -> dict:
    """Simulate + score a model and store everything on the opportunity. Also used for re-scoring."""
    opp = db.one("SELECT * FROM opportunities WHERE id = ?", (opp_id,))
    calib = calibration_for(db, opp["category"])
    sim = simulate(
        model,
        hours_per_week=cfg.profile.hours_per_week,
        horizon_days=cfg.strategy.horizon_days,
        runs=cfg.strategy.simulations,
        calibration=calib,
        seed=opp_id,
    )
    verdict = compliance.stored_verdict(opp)
    value, parts = score(model, sim, verdict, cfg.strategy)
    sim_dict = sim.to_dict() | {"hype_ratio": hype_ratio(opp["claimed_monthly_usd"], sim)}
    values = {
        "status": "researched" if opp["status"] in ("candidate", "researched") else opp["status"],
        "model_json": model.model_dump_json(),
        "sim_json": dumps(sim_dict),
        "score": value,
        "score_json": dumps(parts),
        "updated_at": now(),
    }
    if memo:
        values |= {"research_memo": memo, "sources_json": dumps(sources or []), "researched_at": now()}
    db.update("opportunities", opp_id, values)
    db.log("research", f"#{opp_id} {opp['title']}: score {value}, P(any $)={sim.p_any_revenue:.0%}, "
                       f"median 90d ${sim.revenue_median:.0f}, ${sim.hourly_mean:.2f}/h")
    return {"status": "researched", "score": value, "sim": sim_dict}


def rescore_all(cfg: Config, db: DB) -> int:
    """Re-run simulations (e.g. after calibration changed). No API calls."""
    n = 0
    for row in db.all("SELECT id, model_json FROM opportunities WHERE model_json IS NOT NULL"):
        apply_model(cfg, db, row["id"], EconomicModel.model_validate(json.loads(row["model_json"])))
        n += 1
    return n
