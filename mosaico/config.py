"""Configuration loaded from a TOML file, with safe defaults for every field."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class ModelsConfig:
    # Bulk, cheap work: pulling ideas out of reel captions/transcripts.
    extract: str = "claude-haiku-5-5"
    # Judgment-heavy work: web research, economic modelling, experiment plans.
    research: str = "claude-opus-5-5"
    plan: str = "claude-opus-5-5"
    execute: str = "claude-opus-5-5"
    research_effort: str = "high"
    # Server-side refusal fallback (Claude API only). Disable on Bedrock/Vertex/Foundry.
    server_fallbacks: bool = True


@dataclass
class BudgetConfig:
    # Hard ceiling on Claude API spend per UTC day. The agent idles once it is hit.
    daily_usd: float = 2.00
    # Upper bound used to refuse a single call that could blow through the budget.
    max_call_usd: float = 0.75


@dataclass
class SourcesConfig:
    hashtags: list[str] = field(default_factory=lambda: [
        "aisidehustle", "makemoneywithai", "chatgptmoney", "aibusiness",
        "passiveincomeai", "sidehustleideas", "aitools", "digitalproducts",
    ])
    search_terms: list[str] = field(default_factory=lambda: [
        "make money with AI no money", "AI side hustle for beginners",
    ])
    # Apify (https://apify.com) runs the Instagram scraping on their infrastructure.
    # Free plan includes monthly credits. Token is read from APIFY_TOKEN if empty here.
    apify_token: str = ""
    apify_actor: str = "apify~instagram-hashtag-scraper"
    reels_per_hashtag: int = 20
    # Drop reel URLs (one per line) in this file; they are fetched with yt-dlp.
    url_inbox: str = "data/inbox.txt"
    # Netscape-format cookies exported from your own browser session (optional).
    cookies_file: str = ""
    # Download audio and transcribe with faster-whisper when installed.
    transcribe: bool = True
    whisper_model: str = "base"
    # Only keep reels at least this popular (views or plays). 0 disables the filter.
    min_views: int = 5000
    max_age_days: int = 120


@dataclass
class ProfileConfig:
    """Who is executing the ideas. Used to judge fit, not to filter hard."""
    hours_per_week: float = 10
    skills: list[str] = field(default_factory=lambda: ["writing", "basic design", "spreadsheets"])
    languages: list[str] = field(default_factory=lambda: ["English", "Spanish"])
    country: str = "Spain"
    # Cash you are willing to put in. The user asked for zero-investment ideas.
    max_upfront_usd: float = 0
    # Anything you never want proposed, e.g. "crypto", "dropshipping".
    excluded_topics: list[str] = field(default_factory=list)


@dataclass
class StrategyConfig:
    horizon_days: int = 90
    simulations: int = 4000
    # Research at most this many new opportunities per day (each costs ~$0.20-0.40).
    research_per_day: int = 4
    # Experiments run in parallel; more than ~3 dilutes your available hours.
    max_active_experiments: int = 3
    min_score_to_plan: float = 55.0
    # Minimum mentions across reels before an idea is worth researching.
    min_mentions: int = 1
    weights: dict[str, float] = field(default_factory=lambda: {
        "ev_per_hour": 0.30,
        "p_any_revenue": 0.20,
        "speed": 0.10,
        "evidence": 0.15,
        "automation": 0.10,
        "fit": 0.10,
        "risk": 0.05,
    })


@dataclass
class ScheduleConfig:
    ingest_every_minutes: int = 360
    tick_seconds: int = 60
    digest_hour_utc: int = 8


@dataclass
class NotifyConfig:
    # Slack/Discord-compatible incoming webhook. Read from MOSAICO_WEBHOOK_URL if empty.
    webhook_url: str = ""


@dataclass
class Config:
    data_dir: str = "data"
    workspace_dir: str = "workspace"
    models: ModelsConfig = field(default_factory=ModelsConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    base_dir: Path = field(default_factory=Path.cwd)

    @property
    def db_path(self) -> Path:
        return self.path(self.data_dir) / "mosaico.db"

    def path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.base_dir / q

    @property
    def apify_token(self) -> str:
        return self.sources.apify_token or os.environ.get("APIFY_TOKEN", "")

    @property
    def webhook_url(self) -> str:
        return self.notify.webhook_url or os.environ.get("MOSAICO_WEBHOOK_URL", "")


def _merge(obj: Any, data: dict[str, Any], where: str) -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"Unknown config key '{where}{key}'")
        current = getattr(obj, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"Config section '{where}{key}' must be a table")
            _merge(current, value, f"{where}{key}.")
        elif isinstance(current, dict) and isinstance(value, dict):
            current.update(value)
        else:
            setattr(obj, key, value)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    if path is None:
        candidate = Path(os.environ.get("MOSAICO_CONFIG", "config.toml"))
        path = candidate if candidate.exists() else None
    if path is not None:
        path = Path(path)
        with path.open("rb") as fh:
            _merge(cfg, tomllib.load(fh), "")
        cfg.base_dir = path.resolve().parent
    return cfg
