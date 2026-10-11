"""Monte Carlo "backtest" of a business idea.

You can't replay history for a side hustle the way you can for a trading strategy, so the
backtest is: take the evidence-based economic model from research (ranges, not point
estimates), simulate thousands of 90-day attempts by someone with the operator's hours, and
look at the distribution of outcomes. Per-category calibration factors learned from your
real experiments then correct the model's optimism over time (forward-testing).
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import asdict, dataclass

from .models import EconomicModel, Range

WEEKS_PER_MONTH = 4.345


@dataclass
class SimResult:
    runs: int
    horizon_days: int
    p_any_revenue: float
    p_over_100: float
    p_over_1000: float
    revenue_mean: float
    revenue_p10: float
    revenue_median: float
    revenue_p90: float
    hours_mean: float
    hourly_mean: float
    days_to_first_dollar_median: float | None
    monthly_run_rate_median: float
    expected_weekly_revenue: list[float]
    calibration: float

    def to_dict(self) -> dict:
        return asdict(self)


def tri(rng: random.Random, r: Range, lo: float = 0.0, hi: float | None = None) -> float:
    a, m, b = sorted((r.low, r.mode, r.high))
    a, b = max(lo, a), max(lo, b)
    m = min(max(m, a), b)
    if hi is not None:
        a, m, b = min(a, hi), min(m, hi), min(b, hi)
    if a == b:
        return a
    return rng.triangular(a, b, m)


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[idx]


def simulate(
    model: EconomicModel,
    hours_per_week: float,
    horizon_days: int = 90,
    runs: int = 4000,
    calibration: float = 1.0,
    seed: int = 0,
) -> SimResult:
    rng = random.Random(seed)
    weeks = max(1, horizon_days // 7)
    hours_per_week = max(0.5, hours_per_week)
    weekly_churn = 1 - (1 - min(max(model.monthly_churn, 0), 1)) ** (1 / WEEKS_PER_MONTH)
    weekly_shutdown = 1 - (1 - min(max(model.p_shutdown_per_month, 0), 1)) ** (1 / WEEKS_PER_MONTH)
    fee_keep = 1 - min(max(model.fee_pct, 0), 0.95)

    totals, hours_all, first_days, run_rates = [], [], [], []
    weekly_sum = [0.0] * weeks

    for _ in range(runs):
        launch_hours = tri(rng, model.hours_to_launch, lo=0.5)
        weekly_hours = tri(rng, model.weekly_hours, lo=0.25)
        reach = tri(rng, model.weekly_reach_at_maturity)
        conv = tri(rng, model.conversion_rate, hi=1.0)
        price = tri(rng, model.price_usd)
        ramp = max(1.0, tri(rng, model.ramp_weeks, lo=1.0))
        if rng.random() < model.p_no_traction:
            reach *= 0.03
        reach *= calibration
        # Not enough hours to sustain the work → proportionally less output.
        capacity = min(1.0, hours_per_week / weekly_hours)

        launch_weeks = launch_hours / hours_per_week
        total, hours, customers, first_day, alive = 0.0, 0.0, 0.0, None, True
        last_week_rev = 0.0
        for w in range(weeks):
            if w < launch_weeks:
                hours += min(hours_per_week, launch_hours - w * hours_per_week)
                last_week_rev = 0.0
                continue
            if alive and rng.random() < weekly_shutdown:
                alive = False
            if not alive:
                last_week_rev = 0.0
                continue
            hours += weekly_hours * capacity
            k = w - launch_weeks
            lam = reach * capacity * conv * min(1.0, (k + 1) / ramp)
            sales = poisson(rng, lam)
            if model.recurring:
                customers = customers * (1 - weekly_churn) + sales
                rev = customers * price / WEEKS_PER_MONTH * fee_keep
            else:
                rev = sales * price * fee_keep
            if rev > 0 and first_day is None:
                first_day = (w + 1) * 7
            total += rev
            weekly_sum[w] += rev
            last_week_rev = rev
        totals.append(total)
        hours_all.append(hours)
        run_rates.append(last_week_rev * WEEKS_PER_MONTH)
        if first_day is not None:
            first_days.append(first_day)

    totals_sorted = sorted(totals)
    hours_mean = statistics.fmean(hours_all)
    revenue_mean = statistics.fmean(totals)
    return SimResult(
        runs=runs,
        horizon_days=horizon_days,
        p_any_revenue=sum(t > 0 for t in totals) / runs,
        p_over_100=sum(t >= 100 for t in totals) / runs,
        p_over_1000=sum(t >= 1000 for t in totals) / runs,
        revenue_mean=revenue_mean,
        revenue_p10=percentile(totals_sorted, 0.10),
        revenue_median=percentile(totals_sorted, 0.50),
        revenue_p90=percentile(totals_sorted, 0.90),
        hours_mean=hours_mean,
        hourly_mean=revenue_mean / hours_mean if hours_mean else 0.0,
        days_to_first_dollar_median=statistics.median(first_days) if len(first_days) >= runs * 0.05 else None,
        monthly_run_rate_median=statistics.median(run_rates),
        expected_weekly_revenue=[round(v / runs, 2) for v in weekly_sum],
        calibration=calibration,
    )
