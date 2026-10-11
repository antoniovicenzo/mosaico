"""Command-line interface: `mosaico <command>`."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from . import executor, extract, ingest, planner, report, research, tracker
from .config import Config, load_config
from .db import DB, loads, now
from .sources import save_reels
from .sources import files as file_source
from .sources import inbox, seed


def _llm(cfg: Config, db: DB):
    from .llm import ClaudeLLM

    return ClaudeLLM(cfg, db)


def _opp_id(db: DB, ref: str) -> int:
    row = db.one("SELECT id FROM opportunities WHERE id = ? OR slug = ?", (ref if ref.isdigit() else -1, ref))
    if row is None:
        raise SystemExit(f"No opportunity '{ref}'")
    return row["id"]


# -- commands -------------------------------------------------------------------------

def cmd_init(cfg: Config, db: DB, args) -> None:
    example = Path(__file__).resolve().parent.parent / "config.example.toml"
    target = Path("config.toml")
    if target.exists():
        print("config.toml already exists; leaving it alone.")
    else:
        if example.exists():
            shutil.copy(example, target)
        else:  # installed as a package without the repo checkout
            target.write_text('[profile]\nhours_per_week = 10\ncountry = "Spain"\n\n[budget]\ndaily_usd = 2.00\n')
        print("Wrote config.toml — edit your profile and budget there.")
    inbox_path = cfg.path(cfg.sources.url_inbox)
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    inbox_path.touch()
    print(f"Database: {cfg.db_path}\nURL inbox: {inbox_path}")
    print("Next: export ANTHROPIC_API_KEY=... (and optionally APIFY_TOKEN=...), then `mosaico run`.")


def cmd_run(cfg: Config, db: DB, args) -> None:
    from .scheduler import Agent

    agent = Agent(cfg, db, _llm(cfg, db))
    if args.once:
        agent.tick()
        print(report.digest_text(db))
    else:
        print(f"Mosaico running (tick every {cfg.schedule.tick_seconds}s, budget ${cfg.budget.daily_usd:.2f}/day). Ctrl+C to stop.")
        agent.run_forever()


def cmd_ingest(cfg: Config, db: DB, args) -> None:
    added = 0
    if args.seed:
        added += save_reels(db, seed.sample_reels())
    if args.file:
        added += save_reels(db, file_source.load(args.file))
    if args.url:
        inbox.append_urls(cfg, args.url)
    if args.url or not (args.seed or args.file):
        added += ingest.run(cfg, db)
    print(f"{added} new reels stored.")


def cmd_extract(cfg: Config, db: DB, args) -> None:
    n = extract.extract_pending(db, _llm(cfg, db), limit=args.limit)
    print(f"Processed {n} reels.")


def cmd_research(cfg: Config, db: DB, args) -> None:
    llm = _llm(cfg, db)
    ids = [_opp_id(db, args.id)] if args.id else [r["id"] for r in research.due_for_research(cfg, db)[: args.limit]]
    if not ids:
        print("Nothing to research (no candidates, or today's research quota is used).")
    for opp_id in ids:
        result = research.research_opportunity(cfg, db, llm, opp_id)
        print(f"#{opp_id}: {result['status']}" + (f", score {result['score']}" if "score" in result else ""))


def cmd_plan(cfg: Config, db: DB, args) -> None:
    llm = _llm(cfg, db)
    if args.id:
        ids = [_opp_id(db, args.id)]
    else:
        free = cfg.strategy.max_active_experiments - planner.active_count(db)
        ids = [r["id"] for r in planner.candidates(cfg, db)[: max(0, free)]]
    if not ids:
        print("No opportunity qualifies (score threshold / experiment slots).")
    for opp_id in ids:
        print(f"Started experiment #{planner.plan_experiment(cfg, db, llm, opp_id)}")


def cmd_execute(cfg: Config, db: DB, args) -> None:
    llm = _llm(cfg, db)
    for _ in range(args.limit):
        task = executor.next_task(db)
        if task is None:
            break
        print(f"Task #{task['id']} → {executor.run_task(cfg, db, llm, task)}")


def cmd_status(cfg: Config, db: DB, args) -> None:
    print(report.digest_text(db))


def cmd_list(cfg: Config, db: DB, args) -> None:
    where = "" if args.all else "WHERE status NOT IN ('rejected')"
    rows = db.all(f"SELECT * FROM opportunities {where} ORDER BY score IS NULL, score DESC, mentions DESC")
    for o in rows:
        sim = loads(o["sim_json"], {})
        score = f"{o['score']:5.1f}" if o["score"] is not None else "  -  "
        extra = f"P$ {sim['p_any_revenue']:.0%} med ${sim['revenue_median']:.0f} ${sim['hourly_mean']:.2f}/h" if sim else ""
        print(f"#{o['id']:<4} {score} {o['status']:<10} {o['mentions']:>3}x  {o['title'][:60]:<60} {extra}")


def cmd_show(cfg: Config, db: DB, args) -> None:
    o = db.one("SELECT * FROM opportunities WHERE id = ?", (_opp_id(db, args.id),))
    print(f"#{o['id']} {o['title']}  [{o['status']}]  score={o['score']}")
    print(o["summary"] or "")
    if o["rejected_reason"]:
        print(f"\nRejected/parked: {o['rejected_reason']}")
    model = loads(o["model_json"], {})
    if model:
        print(f"\nVerdict: {model['verdict_summary']}")
        print("Evidence:\n  - " + "\n  - ".join(model["real_world_evidence"]))
        print("Risks:\n  - " + "\n  - ".join(model["key_risks"]))
    sim = loads(o["sim_json"], {})
    if sim:
        print(
            f"\nBacktest ({sim['runs']} runs, {sim['horizon_days']} days): P(any $) {sim['p_any_revenue']:.0%}, "
            f"P(≥$100) {sim['p_over_100']:.0%}, P(≥$1000) {sim['p_over_1000']:.0%}\n"
            f"Revenue P10/median/P90: ${sim['revenue_p10']:.0f} / ${sim['revenue_median']:.0f} / ${sim['revenue_p90']:.0f}"
            f"  ·  ${sim['hourly_mean']:.2f}/hour  ·  first $ ~{sim['days_to_first_dollar_median'] or 'n/a'} days"
            + (f"\nReels' claims are {sim['hype_ratio']}x the simulated typical run-rate." if sim.get("hype_ratio") else "")
        )
    for e in db.all("SELECT * FROM experiments WHERE opportunity_id = ?", (o["id"],)):
        print(f"\nExperiment #{e['id']} [{e['status']}] deadline {e['deadline'][:10]}  totals {tracker.totals(db, e['id'])}")
        for t in db.all("SELECT * FROM tasks WHERE experiment_id = ? ORDER BY idx", (e["id"],)):
            print(f"  #{t['id']:<4} {t['kind']:<5} {t['status']:<6} {t['title']}" + (f"  → {t['output_path']}" if t["output_path"] else ""))


def cmd_tasks(cfg: Config, db: DB, args) -> None:
    rows = report.human_tasks(db)
    if not rows:
        print("Nothing waiting on you.")
    for t in rows:
        d = loads(t["detail"], {})
        print(f"#{t['id']} [{t['opp_title']}] {t['title']}\n    {d.get('detail', '')}\n    Done when: {d.get('deliverable', '')}\n")


def cmd_done(cfg: Config, db: DB, args) -> None:
    db.update("tasks", args.task_id, {"status": "done", "result": args.note or "done", "updated_at": now()})
    print(f"Task #{args.task_id} marked done.")


def cmd_log(cfg: Config, db: DB, args) -> None:
    metrics = {}
    for kv in args.metric or []:
        k, _, v = kv.partition("=")
        metrics[k.strip()] = float(v)
    opp_id = _opp_id(db, args.opportunity)
    tracker.log_outcome(db, opp_id, args.revenue, args.hours, metrics, args.note or "")
    decisions = tracker.evaluate_experiments(db)
    print(f"Logged for #{opp_id}." + (f" Decisions: {decisions}" if decisions else ""))


def cmd_report(cfg: Config, db: DB, args) -> None:
    print(report.write_report(cfg, db))


def cmd_spend(cfg: Config, db: DB, args) -> None:
    for r in db.all(
        "SELECT day, COUNT(*) n, SUM(cost_usd) c, SUM(web_searches) s FROM llm_calls GROUP BY day ORDER BY day DESC LIMIT 14"
    ):
        print(f"{r['day']}  {r['n']:>4} calls  {r['s'] or 0:>3} searches  ${r['c']:.3f}")


def cmd_retry(cfg: Config, db: DB, args) -> None:
    a = db.execute("UPDATE reels SET status = 'new', error = NULL WHERE status = 'error' AND caption IS NOT NULL").rowcount
    b = db.execute("UPDATE opportunities SET status = CASE WHEN model_json IS NULL THEN 'candidate' ELSE 'researched' END, "
                   "rejected_reason = NULL WHERE status = 'error'").rowcount
    c = db.execute("UPDATE tasks SET status = 'todo' WHERE status = 'failed'").rowcount
    print(f"Requeued {a} reels, {b} opportunities, {c} tasks.")


def cmd_rescore(cfg: Config, db: DB, args) -> None:
    print(f"Re-simulated {research.rescore_all(cfg, db)} opportunities.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mosaico", description=__doc__)
    p.add_argument("--config", help="Path to config.toml (default: ./config.toml or $MOSAICO_CONFIG)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("init", cmd_init, "Create config.toml and data folders")
    add("run", cmd_run, "Run the agent continuously").add_argument("--once", action="store_true", help="One tick, then exit")
    sp = add("ingest", cmd_ingest, "Fetch reels (Apify + URL inbox), or import samples/files")
    sp.add_argument("--seed", action="store_true", help="Load built-in sample reels")
    sp.add_argument("--file", help="Import JSON/JSONL/CSV of reels")
    sp.add_argument("--url", nargs="+", help="Add reel URLs to the inbox and fetch them")
    add("extract", cmd_extract, "Extract ideas from pending reels").add_argument("--limit", type=int, default=50)
    sp = add("research", cmd_research, "Backtest opportunities (web research + simulation)")
    sp.add_argument("--id", help="Opportunity id or slug (ignores the daily quota)")
    sp.add_argument("--limit", type=int, default=1)
    add("plan", cmd_plan, "Start experiments for top opportunities").add_argument("--id")
    add("execute", cmd_execute, "Produce assets for agent tasks").add_argument("--limit", type=int, default=3)
    add("status", cmd_status, "Summary")
    add("ls", cmd_list, "List opportunities").add_argument("--all", action="store_true")
    add("show", cmd_show, "Details of one opportunity").add_argument("id")
    add("tasks", cmd_tasks, "Tasks waiting on you")
    sp = add("done", cmd_done, "Mark one of your tasks done")
    sp.add_argument("task_id", type=int)
    sp.add_argument("--note")
    sp = add("log", cmd_log, "Log real results for an opportunity")
    sp.add_argument("opportunity")
    sp.add_argument("--revenue", type=float, default=0.0)
    sp.add_argument("--hours", type=float, default=0.0)
    sp.add_argument("--metric", nargs="*", help="name=value, e.g. visits=120 sales=2")
    sp.add_argument("--note")
    add("report", cmd_report, "Write the HTML dashboard")
    add("spend", cmd_spend, "API spend per day")
    add("retry", cmd_retry, "Requeue failed items")
    add("rescore", cmd_rescore, "Re-run simulations with current calibration")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    cfg = load_config(args.config)
    db = DB(cfg.db_path)
    try:
        args.fn(cfg, db, args)
    except KeyboardInterrupt:
        return 130
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
