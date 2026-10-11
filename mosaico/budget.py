"""Spending phases, so the total cost stays small and predictable without babysitting.

1. discovery    First `discovery_days` days. Ingest, extract, research and backtest as much as
                `discovery_usd` allows (a hard cap on the whole phase, paced evenly across the days).
                Ends early if that money is spent.
2. maintenance  No new research. Only planning, writing experiment assets and tracking, within
                `maintenance_daily_usd` and `maintenance_monthly_usd`.
3. paused       Set automatically once maintenance has run `breakeven_days` and the revenue you
                logged is still below total API spend. No more API calls until `mosaico resume`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .config import Config
from .db import DB, today

DISCOVERY, MAINTENANCE, PAUSED = "discovery", "maintenance", "paused"


def _spent(db: DB, since: str | None = None, day: str | None = None) -> float:
    if day is not None:
        sql, args = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE day = ?", (day,)
    elif since is not None:
        sql, args = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE day >= ?", (since,)
    else:
        sql, args = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls", ()
    return float(db.scalar(sql, args) or 0)


def start_date(db: DB) -> date:
    started = db.get_kv("started_on")
    if started is None:
        started = today()
        db.set_kv("started_on", started)
    return date.fromisoformat(started)


@dataclass
class Status:
    phase: str
    day: int                 # 1-based day number since start
    allowance: float         # USD that may still be spent right now
    spent_total: float
    spent_today: float
    discovery_spent: float
    revenue_total: float
    note: str


def status(cfg: Config, db: DB) -> Status:
    b = cfg.budget
    start = start_date(db)
    now = datetime.now(timezone.utc).date()
    day = (now - start).days + 1
    ended = db.get_kv("discovery_ended_on")
    discovery_end = date.fromisoformat(ended) if ended else start + timedelta(days=b.discovery_days)
    spent_total = _spent(db)
    spent_today = _spent(db, day=today())
    discovery_spent = float(db.scalar(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE day < ?", (discovery_end.isoformat(),)
    ) or 0)
    revenue = float(db.scalar("SELECT COALESCE(SUM(revenue_usd), 0) FROM outcomes") or 0)

    def make(phase, allowance, note):
        return Status(phase, day, max(0.0, allowance), spent_total, spent_today, discovery_spent, revenue, note)

    if db.get_kv("paused"):
        return make(PAUSED, 0.0, db.get_kv("paused") or "")

    discovery_left = b.discovery_usd - discovery_spent
    if now < discovery_end and discovery_left > b.min_call_usd:
        # Pace the phase budget evenly over the remaining days (today included).
        days_left = (discovery_end - now).days
        before_today = discovery_spent - spent_today
        today_quota = (b.discovery_usd - before_today) / max(days_left, 1)
        allowance = min(discovery_left, today_quota - spent_today, b.daily_usd - spent_today)
        return make(DISCOVERY, allowance, f"day {day} of {b.discovery_days}, ${discovery_left:.2f} of ${b.discovery_usd:.2f} left")

    if not ended:
        # Remember when discovery really ended (budget can run out before the last day), so that
        # every dollar after it counts toward the maintenance caps.
        discovery_end = min(now, discovery_end)
        db.set_kv("discovery_ended_on", discovery_end.isoformat())
    # Only spend after discovery counts toward the maintenance month.
    month_spent = _spent(db, since=max(now - timedelta(days=29), discovery_end).isoformat())
    allowance = min(b.maintenance_daily_usd - spent_today, b.maintenance_monthly_usd - month_spent)
    return make(MAINTENANCE, allowance, f"${month_spent:.2f} of ${b.maintenance_monthly_usd:.2f} used in the last 30 days")


def check_breakeven(cfg: Config, db: DB) -> str | None:
    """Pause all spending if the experiment hasn't paid for itself. Returns the reason if it paused now."""
    if db.get_kv("paused") or cfg.budget.breakeven_days <= 0:
        return None
    s = status(cfg, db)
    if s.phase != MAINTENANCE:
        return None
    ended = db.get_kv("discovery_ended_on")
    if not ended:
        return None
    maintenance_days = (date.fromisoformat(today()) - date.fromisoformat(ended)).days
    if maintenance_days < cfg.budget.breakeven_days:
        return None
    resumed = db.get_kv("resumed_on")
    if resumed and (date.fromisoformat(today()) - date.fromisoformat(resumed)).days < cfg.budget.breakeven_days:
        return None
    if s.revenue_total >= s.spent_total:
        return None
    reason = (
        f"Auto-paused: after {maintenance_days} days of maintenance, logged revenue ${s.revenue_total:.2f} "
        f"is below API spend ${s.spent_total:.2f}. Run `mosaico resume` to continue anyway."
    )
    db.set_kv("paused", reason)
    db.log("budget", reason, "warn")
    return reason


def resume(db: DB) -> None:
    db.execute("DELETE FROM kv WHERE key = 'paused'")
    db.set_kv("resumed_on", today())
    db.log("budget", "Resumed by operator")
