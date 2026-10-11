"""Turn simulation results + research judgments into one comparable 0-100 score."""

from __future__ import annotations

from .config import StrategyConfig
from .models import ComplianceVerdict, EconomicModel
from .simulate import SimResult


def components(model: EconomicModel, sim: SimResult, verdict: ComplianceVerdict | None) -> dict[str, float]:
    days = sim.days_to_first_dollar_median
    caution = 1.0 if verdict is not None and verdict.verdict == "caution" else 0.0
    risk = (
        0.45 * min(max(model.saturation, 0), 10) / 10
        + 0.30 * caution
        + 0.25 * min(1.0, model.p_shutdown_per_month * 12)
    )
    return {
        # $15/hour scores 0.5; saturates toward 1 for very high hourly value.
        "ev_per_hour": max(sim.hourly_mean, 0) / (max(sim.hourly_mean, 0) + 15),
        "p_any_revenue": sim.p_any_revenue,
        "speed": 0.0 if days is None else max(0.0, 1 - days / sim.horizon_days),
        "evidence": min(max(model.evidence_quality, 0), 10) / 10,
        "automation": min(max(model.automation_potential, 0), 10) / 10,
        "fit": min(max(model.skill_fit, 0), 10) / 10,
        "risk": max(0.0, 1 - risk),
    }


def score(model: EconomicModel, sim: SimResult, verdict: ComplianceVerdict | None, strategy: StrategyConfig) -> tuple[float, dict[str, float]]:
    comps = components(model, sim, verdict)
    weights = strategy.weights
    total_w = sum(weights.get(k, 0) for k in comps) or 1.0
    value = 100 * sum(comps[k] * weights.get(k, 0) for k in comps) / total_w
    return round(value, 1), {k: round(v, 3) for k, v in comps.items()}


def hype_ratio(claimed_monthly: float | None, sim: SimResult) -> float | None:
    """How many times larger the reels' income claims are than the simulated typical run-rate."""
    if not claimed_monthly:
        return None
    return round(claimed_monthly / max(sim.monthly_run_rate_median, 1.0), 1)
