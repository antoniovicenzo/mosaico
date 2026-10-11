"""Static HTML dashboard and plain-text digests."""

from __future__ import annotations

import html
from pathlib import Path

from . import budget
from .config import Config
from .db import DB, loads, today


def summary(db: DB, cfg: Config | None = None) -> dict:
    count = lambda sql, *a: db.scalar(sql, a) or 0  # noqa: E731
    phase = budget.status(cfg or Config(), db)
    return {
        "phase": phase.phase,
        "phase_note": phase.note,
        "reels": count("SELECT COUNT(*) FROM reels"),
        "reels_pending": count("SELECT COUNT(*) FROM reels WHERE status = 'new'"),
        "ideas": count("SELECT COUNT(*) FROM ideas"),
        "opportunities": count("SELECT COUNT(*) FROM opportunities"),
        "rejected": count("SELECT COUNT(*) FROM opportunities WHERE status = 'rejected'"),
        "researched": count("SELECT COUNT(*) FROM opportunities WHERE score IS NOT NULL"),
        "running": count("SELECT COUNT(*) FROM experiments WHERE status = 'running'"),
        "human_todo": count(
            "SELECT COUNT(*) FROM tasks t JOIN experiments e ON e.id = t.experiment_id "
            "WHERE t.kind = 'human' AND t.status = 'todo' AND e.status = 'running'"
        ),
        "revenue_total": float(count("SELECT COALESCE(SUM(revenue_usd), 0) FROM outcomes")),
        "hours_total": float(count("SELECT COALESCE(SUM(hours), 0) FROM outcomes")),
        "spend_today": float(count("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE day = ?", today())),
        "spend_total": float(count("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls")),
    }


def ranked(db: DB, limit: int = 50) -> list:
    return db.all(
        "SELECT * FROM opportunities WHERE score IS NOT NULL AND status != 'rejected' ORDER BY score DESC LIMIT ?",
        (limit,),
    )


def human_tasks(db: DB) -> list:
    return db.all(
        """SELECT t.*, o.title AS opp_title, o.id AS opp_id FROM tasks t
           JOIN experiments e ON e.id = t.experiment_id JOIN opportunities o ON o.id = e.opportunity_id
           WHERE t.kind = 'human' AND t.status = 'todo' AND e.status = 'running'
           ORDER BY t.experiment_id, t.idx"""
    )


def digest_text(db: DB, cfg: Config | None = None) -> str:
    s = summary(db, cfg)
    lines = [
        f"Mosaico daily digest — {today()}",
        f"Reels: {s['reels']} ({s['reels_pending']} pending) · Opportunities: {s['opportunities']} "
        f"({s['researched']} backtested, {s['rejected']} rejected)",
        f"Experiments running: {s['running']} · Your to-dos: {s['human_todo']}",
        f"Real revenue logged: ${s['revenue_total']:.2f} over {s['hours_total']:.1f} h · "
        f"API spend: ${s['spend_today']:.2f} today, ${s['spend_total']:.2f} total",
        f"Budget phase: {s['phase']} — {s['phase_note']}",
        "",
        "Top opportunities:",
    ]
    for o in ranked(db, 5):
        sim = loads(o["sim_json"], {})
        lines.append(
            f"  #{o['id']} {o['score']:.0f}/100 {o['title']} — P(any $) {sim.get('p_any_revenue', 0):.0%}, "
            f"median 90d ${sim.get('revenue_median', 0):.0f}, ${sim.get('hourly_mean', 0):.2f}/h [{o['status']}]"
        )
    return "\n".join(lines)


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def render_html(db: DB, cfg: Config | None = None) -> str:
    e = html.escape
    s = summary(db, cfg)
    rows = []
    for o in ranked(db):
        sim = loads(o["sim_json"], {})
        model = loads(o["model_json"], {})
        hype = sim.get("hype_ratio")
        days = sim.get("days_to_first_dollar_median")
        sources = loads(o["sources_json"], [])
        src = "".join(f'<li><a href="{e(x["url"])}" rel="noopener noreferrer">{e(x.get("title") or x["url"])}</a></li>' for x in sources[:12])
        rows.append(f"""
<details class="opp">
  <summary>
    <span class="score">{o['score']:.0f}</span>
    <span class="title">{e(o['title'])}<small>{e(o['category'] or '')} · {e(o['status'])} · {o['mentions']} reels</small></span>
    <span class="num">{sim.get('p_any_revenue', 0):.0%}</span>
    <span class="num">{_money(sim.get('revenue_median'))}</span>
    <span class="num">{_money(sim.get('revenue_p90'))}</span>
    <span class="num">${sim.get('hourly_mean', 0):.2f}</span>
    <span class="num">{f'{days:.0f}d' if days else 'never'}</span>
  </summary>
  <div class="body">
    <p>{e(o['summary'] or '')}</p>
    <p><b>Verdict:</b> {e(model.get('verdict_summary', ''))}</p>
    <p><b>Reels claim:</b> {_money(o['claimed_monthly_usd'])}/month{f' — {hype}× the simulated typical run-rate' if hype else ''}</p>
    <p><b>Key risks:</b> {e('; '.join(model.get('key_risks', [])))}</p>
    <details><summary>Research memo</summary><pre>{e(o['research_memo'] or '')}</pre></details>
    <details><summary>Sources ({len(sources)})</summary><ul>{src}</ul></details>
  </div>
</details>""")

    todos = "".join(
        f"<li><b>#{t['id']}</b> [{e(t['opp_title'])}] {e(t['title'])}<br><small>{e(loads(t['detail'], {}).get('detail', ''))}</small></li>"
        for t in human_tasks(db)
    ) or "<li>Nothing waiting on you.</li>"
    rejected = "".join(
        f"<li>{e(o['title'])} — <small>{e(o['rejected_reason'] or '')}</small></li>"
        for o in db.all("SELECT * FROM opportunities WHERE status = 'rejected' ORDER BY total_views DESC LIMIT 30")
    ) or "<li>None yet.</li>"
    exps = "".join(
        f"<tr><td>#{x['id']}</td><td>{e(x['title'])}</td><td>{e(x['status'])}</td><td>{e(x['deadline'][:10])}</td>"
        f"<td>{e(x['decision_note'] or '')}</td></tr>"
        for x in db.all(
            "SELECT e.*, o.title FROM experiments e JOIN opportunities o ON o.id = e.opportunity_id ORDER BY e.id DESC LIMIT 30"
        )
    ) or '<tr><td colspan="5">No experiments yet.</td></tr>'
    events = "".join(
        f"<li><small>{e(x['ts'][5:16])}</small> <b>{e(x['kind'])}</b> {e(x['message'])}</li>"
        for x in db.all("SELECT * FROM events ORDER BY id DESC LIMIT 40")
    )

    stats = [
        ("Reels analysed", f"{s['reels'] - s['reels_pending']:,}"),
        ("Opportunities backtested", f"{s['researched']:,}"),
        ("Rejected as unsafe", f"{s['rejected']:,}"),
        ("Experiments running", f"{s['running']}"),
        ("Revenue logged", f"${s['revenue_total']:,.2f}"),
        ("API spend (total)", f"${s['spend_total']:,.2f}"),
        ("Budget phase", s["phase"]),
    ]
    tiles = "".join(f'<div class="tile"><div class="v">{v}</div><div class="k">{k}</div></div>' for k, v in stats)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mosaico Dashboard</title>
<style>
:root {{ --bg:#f7f7f5; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#e3e3de; --accent:#2b59c3; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --fg:#ececea; --muted:#a3a39d; --card:#1e1e1c; --line:#33332f; --accent:#7ea2ff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:32px 0 10px; }}
.muted, small {{ color:var(--muted); }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:16px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }}
.tile .v {{ font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; }} .tile .k {{ color:var(--muted); font-size:13px; }}
.head, .opp > summary {{ display:grid; grid-template-columns:48px 1fr repeat(5, 84px); gap:8px; align-items:center; }}
.head {{ font-size:12px; color:var(--muted); padding:0 12px; }}
.opp {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin:6px 0; }}
.opp > summary {{ padding:10px 12px; cursor:pointer; list-style:none; }}
.score {{ font-weight:700; font-size:18px; color:var(--accent); }}
.title small {{ display:block; }} .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.body {{ padding:0 12px 12px; border-top:1px solid var(--line); }}
pre {{ white-space:pre-wrap; background:var(--bg); padding:10px; border-radius:8px; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); }} td {{ border-bottom:1px solid var(--line); padding:6px 8px; vertical-align:top; }}
a {{ color:var(--accent); }}
@media (max-width:700px) {{ .head {{ display:none; }} .opp > summary {{ grid-template-columns:40px 1fr 70px; }} .opp > summary .num:nth-of-type(n+2) {{ display:none; }} }}
</style></head><body><main>
<h1>Mosaico</h1><div class="muted">Updated {e(today())} · reel ideas → compliance → web research → Monte Carlo backtest → experiments</div>
<div class="tiles">{tiles}</div>
<h2>Waiting on you</h2><ul>{todos}</ul>
<h2>Ranked opportunities (90-day backtest)</h2>
<div class="head"><span>Score</span><span>Method</span><span class="num">P(any $)</span><span class="num">Median</span><span class="num">P90</span><span class="num">$/hour</span><span class="num">1st $</span></div>
{''.join(rows) or '<p class="muted">Nothing backtested yet.</p>'}
<h2>Experiments</h2><div style="overflow-x:auto"><table>{exps}</table></div>
<h2>Rejected ideas</h2><ul>{rejected}</ul>
<h2>Activity</h2><ul>{events}</ul>
</main></body></html>"""


def write_report(cfg: Config, db: DB) -> Path:
    path = cfg.path(cfg.data_dir) / "report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(db, cfg), encoding="utf-8")
    return path
