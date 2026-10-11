"""Forward-testing: record real results, judge experiments, and learn calibration factors.

The simulation is only as good as its assumptions. Every finished experiment compares what the
model expected over the experiment window with what actually happened, and the per-category
ratio (shrunk toward 1 until there is enough data) scales future simulations in that category.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from .db import DB, dumps, loads, now, today
from .models import Criterion, ExperimentPlan

BUILTIN = ("revenue_usd", "hours")
SHRINK = 3.0  # pseudo-observations at factor 1.0


def log_outcome(db: DB, opp_id: int, revenue: float = 0.0, hours: float = 0.0,
                metrics: dict[str, float] | None = None, note: str = "", day: str | None = None) -> int:
    exp = db.one(
        "SELECT id FROM experiments WHERE opportunity_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
        (opp_id,),
    )
    return db.insert("outcomes", {
        "opportunity_id": opp_id,
        "experiment_id": exp["id"] if exp else None,
        "day": day or today(),
        "revenue_usd": revenue,
        "hours": hours,
        "metrics_json": dumps(metrics or {}),
        "note": note,
    })


def totals(db: DB, experiment_id: int) -> dict[str, float]:
    out = {"revenue_usd": 0.0, "hours": 0.0}
    for row in db.all("SELECT * FROM outcomes WHERE experiment_id = ?", (experiment_id,)):
        out["revenue_usd"] += row["revenue_usd"] or 0
        out["hours"] += row["hours"] or 0
        for k, v in loads(row["metrics_json"], {}).items():
            out[k] = out.get(k, 0.0) + float(v)
    return out


def met(criterion: Criterion, values: dict[str, float]) -> bool:
    v = values.get(criterion.metric, 0.0)
    return v >= criterion.threshold if criterion.op == ">=" else v <= criterion.threshold


def evaluate_experiments(db: DB) -> list[tuple[int, str]]:
    """Close experiments that hit success criteria or reached their deadline."""
    decisions = []
    for exp in db.all("SELECT * FROM experiments WHERE status = 'running'"):
        plan = ExperimentPlan.model_validate_json(exp["plan_json"])
        values = totals(db, exp["id"])
        deadline = datetime.fromisoformat(exp["deadline"])
        expired = datetime.now(timezone.utc) >= deadline
        success = bool(plan.success_criteria) and all(met(c, values) for c in plan.success_criteria)
        if not (success or expired):
            continue
        status = "succeeded" if success else "killed"
        kill_hits = [f"{c.metric} {c.op} {c.threshold}" for c in plan.kill_criteria if met(c, values)]
        note = (
            f"{'Success criteria met' if success else 'Deadline reached without success'}; "
            f"revenue ${values['revenue_usd']:.2f} over {values['hours']:.1f} h"
            + (f"; kill criteria hit: {', '.join(kill_hits)}" if kill_hits else "")
        )
        db.update("experiments", exp["id"], {"status": status, "decided_at": now(), "decision_note": note})
        db.update("opportunities", exp["opportunity_id"], {
            "status": "scaling" if success else "killed", "updated_at": now(),
        })
        update_calibration(db, exp["id"])
        db.log("experiment", f"Experiment #{exp['id']} {status}: {note}")
        decisions.append((exp["id"], status))
    return decisions


def expected_revenue(sim: dict, days: int) -> float:
    weekly = sim.get("expected_weekly_revenue") or []
    weeks = days / 7
    full = int(weeks)
    value = sum(weekly[:full])
    if full < len(weekly):
        value += weekly[full] * (weeks - full)
    return value


def update_calibration(db: DB, experiment_id: int) -> None:
    exp = db.one("SELECT * FROM experiments WHERE id = ?", (experiment_id,))
    opp = db.one("SELECT category, sim_json FROM opportunities WHERE id = ?", (exp["opportunity_id"],))
    sim = loads(opp["sim_json"], {})
    if not sim:
        return
    start = datetime.fromisoformat(exp["started_at"])
    end = min(datetime.now(timezone.utc), datetime.fromisoformat(exp["deadline"]))
    days = max(1, (end - start).days)
    # The simulation's calibration is already baked into expected revenue; undo it to get the raw ratio.
    predicted = expected_revenue(sim, days) / max(sim.get("calibration", 1.0), 1e-6)
    actual = totals(db, experiment_id)["revenue_usd"]
    ratio = (actual + 5) / (predicted + 5)  # +5 smooths tiny numbers
    state = json.loads(db.get_kv("calibration", "{}"))
    entry = state.setdefault(opp["category"], {"logs": []})
    entry["logs"].append(math.log(ratio))
    db.set_kv("calibration", json.dumps(state, sort_keys=True))


def calibration_for(db: DB, category: str) -> float:
    state = json.loads(db.get_kv("calibration", "{}"))
    logs = state.get(category, {}).get("logs", [])
    if not logs:
        return 1.0
    mean = sum(logs) / (len(logs) + SHRINK)  # shrink toward log(1)=0
    return round(min(5.0, max(0.05, math.exp(mean))), 3)
