"""The always-on loop. Each tick advances every stage a little, within the daily budget."""

from __future__ import annotations

import fcntl
import logging
import signal
import time
from datetime import datetime, timezone

from . import executor, extract, ingest, notify, planner, report, research, tracker
from .config import Config
from .db import DB, today
from .llm import LLM, BudgetExceeded, is_item_error

log = logging.getLogger(__name__)


class Agent:
    def __init__(self, cfg: Config, db: DB, llm: LLM):
        self.cfg = cfg
        self.db = db
        self.llm = llm
        self.stopping = False

    # -- stages -----------------------------------------------------------------
    def ingest_if_due(self) -> None:
        last = self.db.get_kv("last_ingest")
        if last:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
            if age < self.cfg.schedule.ingest_every_minutes:
                return
        self.db.set_kv("last_ingest", datetime.now(timezone.utc).isoformat())
        ingest.run(self.cfg, self.db)

    def extract(self) -> None:
        extract.extract_pending(self.db, self.llm, limit=10)

    def research(self) -> None:
        for opp in research.due_for_research(self.cfg, self.db)[:1]:
            try:
                research.research_opportunity(self.cfg, self.db, self.llm, opp["id"])
            except Exception as e:
                self._park("opportunities", opp["id"], e)

    def plan(self) -> None:
        if planner.active_count(self.db) >= self.cfg.strategy.max_active_experiments:
            return
        for opp in planner.candidates(self.cfg, self.db)[:1]:
            try:
                exp_id = planner.plan_experiment(self.cfg, self.db, self.llm, opp["id"])
            except Exception as e:
                self._park("opportunities", opp["id"], e)
                continue
            notify.send(self.cfg, f"🧪 New experiment #{exp_id}: {opp['title']} (score {opp['score']:.0f}). "
                                  f"Run `mosaico tasks` to see what needs you.")

    def execute(self) -> None:
        task = executor.next_task(self.db)
        if task is not None:
            try:
                executor.run_task(self.cfg, self.db, self.llm, task)
            except Exception as e:
                self._park("tasks", task["id"], e)

    def _park(self, table: str, row_id: int, error: Exception) -> None:
        """Move a failing item out of the queue so one bad item can't burn the budget every tick.

        System-level failures (budget, auth, network, rate limits) are re-raised instead, leaving
        the item queued for a later tick.
        """
        if not is_item_error(error):
            raise error
        msg = f"{error.__class__.__name__}: {error}"[:1000]
        if table == "tasks":
            self.db.update("tasks", row_id, {"status": "failed", "result": msg})
        else:
            self.db.update("opportunities", row_id, {"status": "error", "rejected_reason": msg})
        self.db.log("error", f"{table} #{row_id} parked: {msg}", "error")

    def evaluate(self) -> None:
        decisions = tracker.evaluate_experiments(self.db)
        if decisions:
            research.rescore_all(self.cfg, self.db)
            for exp_id, status in decisions:
                notify.send(self.cfg, f"Experiment #{exp_id} {status}.")

    def digest_if_due(self) -> None:
        now = datetime.now(timezone.utc)
        if now.hour < self.cfg.schedule.digest_hour_utc or self.db.get_kv("last_digest") == today():
            return
        self.db.set_kv("last_digest", today())
        notify.send(self.cfg, report.digest_text(self.db))

    # -- loop -------------------------------------------------------------------
    STAGES = ("ingest_if_due", "evaluate", "extract", "research", "plan", "execute", "digest_if_due")
    LLM_STAGES = {"extract", "research", "plan", "execute"}

    def tick(self) -> None:
        budget_hit = False
        for name in self.STAGES:
            if self.stopping:
                return
            if budget_hit and name in self.LLM_STAGES:
                continue
            try:
                getattr(self, name)()
            except BudgetExceeded as e:
                budget_hit = True
                if self.db.get_kv("budget_note") != today():
                    self.db.set_kv("budget_note", today())
                    self.db.log("budget", str(e), "warn")
            except Exception as e:
                log.exception("Stage %s failed", name)
                self.db.log(name, f"{e.__class__.__name__}: {e}", "error")
        try:
            report.write_report(self.cfg, self.db)
        except Exception:
            log.exception("Report failed")

    def run_forever(self) -> None:
        lock_path = self.cfg.path(self.cfg.data_dir) / "agent.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = lock_path.open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another mosaico agent is already running on this data directory.")

        def stop(*_):
            log.info("Stopping after the current stage…")
            self.stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.db.log("agent", "Agent started")
        while not self.stopping:
            started = time.monotonic()
            self.tick()
            delay = max(1.0, self.cfg.schedule.tick_seconds - (time.monotonic() - started))
            while delay > 0 and not self.stopping:
                time.sleep(min(delay, 1.0))
                delay -= 1.0
        self.db.log("agent", "Agent stopped")
