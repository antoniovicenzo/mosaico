"""SQLite persistence. One file, WAL mode, safe to read while the daemon writes."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS reels (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    url         TEXT,
    author      TEXT,
    caption     TEXT,
    transcript  TEXT,
    views       INTEGER DEFAULT 0,
    likes       INTEGER DEFAULT 0,
    comments    INTEGER DEFAULT 0,
    posted_at   TEXT,
    fetched_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',
    error       TEXT
);

CREATE TABLE IF NOT EXISTS ideas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    reel_id             TEXT NOT NULL REFERENCES reels(id),
    title               TEXT NOT NULL,
    summary             TEXT,
    category            TEXT,
    steps_json          TEXT,
    tools_json          TEXT,
    claimed_monthly_usd REAL,
    claimed_upfront_usd REAL,
    red_flags_json      TEXT,
    hype                REAL,
    opportunity_id      INTEGER REFERENCES opportunities(id),
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunities (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                 TEXT UNIQUE NOT NULL,
    title                TEXT NOT NULL,
    summary              TEXT,
    category             TEXT,
    mentions             INTEGER DEFAULT 0,
    total_views          INTEGER DEFAULT 0,
    claimed_monthly_usd  REAL,
    status               TEXT NOT NULL DEFAULT 'candidate',
    compliance_json      TEXT,
    rejected_reason      TEXT,
    research_memo        TEXT,
    sources_json         TEXT,
    model_json           TEXT,
    sim_json             TEXT,
    score                REAL,
    score_json           TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    researched_at        TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id),
    status          TEXT NOT NULL DEFAULT 'running',
    plan_json       TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    deadline        TEXT NOT NULL,
    decided_at      TEXT,
    decision_note   TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id     INTEGER NOT NULL REFERENCES experiments(id),
    idx               INTEGER NOT NULL,
    title             TEXT NOT NULL,
    detail            TEXT,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'todo',
    output_path       TEXT,
    result            TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id),
    experiment_id   INTEGER REFERENCES experiments(id),
    day             TEXT NOT NULL,
    revenue_usd     REAL DEFAULT 0,
    hours           REAL DEFAULT 0,
    metrics_json    TEXT,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    day            TEXT NOT NULL,
    purpose        TEXT,
    model          TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cache_read     INTEGER,
    cache_write    INTEGER,
    web_searches   INTEGER,
    cost_usd       REAL
);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL,
    kind     TEXT NOT NULL,
    message  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE INDEX IF NOT EXISTS idx_reels_status ON reels(status);
CREATE INDEX IF NOT EXISTS idx_ideas_opp ON ideas(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status);
CREATE INDEX IF NOT EXISTS idx_tasks_exp ON tasks(experiment_id);
CREATE INDEX IF NOT EXISTS idx_calls_day ON llm_calls(day);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.one(sql, params)
        return None if row is None else row[0]

    def insert(self, table: str, values: dict[str, Any], or_ignore: bool = False) -> int:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
        cur = self.conn.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
        return cur.lastrowid if cur.rowcount else 0

    def update(self, table: str, row_id: Any, values: dict[str, Any]) -> None:
        sets = ", ".join(f"{k} = ?" for k in values)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return default if row is None else row["value"]

    def set_kv(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def log(self, kind: str, message: str, level: str = "info") -> None:
        self.conn.execute(
            "INSERT INTO events (ts, level, kind, message) VALUES (?, ?, ?, ?)",
            (now(), level, kind, message[:4000]),
        )
