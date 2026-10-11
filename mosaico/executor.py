"""Do the work an AI can do unattended: produce the experiment's assets as files.

The executor deliberately never publishes, messages, signs up, or spends. Those tasks are queued
for you (`mosaico tasks`), because they need your identity, your accounts, and your judgment, and
because platforms ban accounts that automate them.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .config import Config
from .db import DB, loads, now
from .llm import LLM
from .models import AssetBundle, ExperimentPlan

SYSTEM = """You are producing ready-to-use materials for a small, legitimate online business experiment. \
Write complete, polished, final content, not outlines or placeholders (unless a placeholder is unavoidable, \
e.g. [YOUR NAME], [LINK]). Match the language and tone the target customers expect. Keep claims truthful: \
no invented testimonials, fake scarcity, or income guarantees; mention AI assistance where the plan's \
disclosure note requires it. Prefer Markdown, CSV or plain text files. Split large deliverables into several files."""

MAX_FILE_BYTES = 400_000


def safe_relpath(name: str) -> PurePosixPath | None:
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "/")]
    if not parts or any(p == ".." for p in parts):
        return None
    clean = [re.sub(r"[^A-Za-z0-9._ -]", "_", p)[:80] for p in parts]
    return PurePosixPath(*clean)


def task_dir(cfg: Config, opp_slug: str, exp_id: int, task) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", task["title"].lower()).strip("-")[:40] or "task"
    return cfg.path(cfg.workspace_dir) / opp_slug / f"exp-{exp_id}" / f"{task['idx'] + 1:02d}-{slug}"


def write_bundle(base: Path, bundle: AssetBundle) -> list[Path]:
    base.mkdir(parents=True, exist_ok=True)
    written = []
    for f in bundle.files:
        rel = safe_relpath(f.filename)
        if rel is None:
            continue
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f.content[:MAX_FILE_BYTES], encoding="utf-8")
        written.append(path)
    (base / "NOTES.md").write_text(f"# Review before use\n\n{bundle.notes}\n", encoding="utf-8")
    return written


def next_task(db: DB):
    return db.one(
        """SELECT t.*, e.plan_json, e.opportunity_id, o.slug, o.title AS opp_title, o.summary AS opp_summary
           FROM tasks t JOIN experiments e ON e.id = t.experiment_id JOIN opportunities o ON o.id = e.opportunity_id
           WHERE t.kind = 'agent' AND t.status = 'todo' AND e.status = 'running'
           ORDER BY t.experiment_id, t.idx LIMIT 1"""
    )


def run_task(cfg: Config, db: DB, llm: LLM, task) -> Path:
    plan = ExperimentPlan.model_validate_json(task["plan_json"])
    detail = loads(task["detail"], {})
    done = db.all(
        "SELECT title, output_path FROM tasks WHERE experiment_id = ? AND status = 'done' AND kind = 'agent'",
        (task["experiment_id"],),
    )
    p = cfg.profile
    prompt = (
        f"Business: {task['opp_title']}\n{task['opp_summary']}\n"
        f"Offer: {plan.offer}\nChannel: {plan.channel}\nHypothesis: {plan.hypothesis}\n"
        f"Disclosure: {plan.disclosure_note}\n"
        f"Operator languages: {', '.join(p.languages)}; country: {p.country}\n"
        f"Already produced: {', '.join(r['title'] for r in done) or 'nothing yet'}\n\n"
        f"TASK: {task['title']}\n{detail.get('detail', '')}\nDeliverable: {detail.get('deliverable', '')}"
    )
    bundle = llm.structured(
        purpose=f"execute:{task['id']}", role="execute", system=SYSTEM, prompt=prompt, schema=AssetBundle,
        max_tokens=16000,
    )
    base = task_dir(cfg, task["slug"], task["experiment_id"], task)
    files = write_bundle(base, bundle)
    db.update("tasks", task["id"], {
        "status": "done",
        "output_path": str(base),
        "result": f"{len(files)} files",
        "updated_at": now(),
    })
    db.log("execute", f"Task #{task['id']} '{task['title']}' → {len(files)} files in {base}")
    return base
