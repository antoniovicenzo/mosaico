"""Collect reels from every configured source and store the new ones."""

from __future__ import annotations

import logging

from . import transcribe
from .config import Config
from .db import DB
from .sources import Reel, save_reels
from .sources import apify, inbox

log = logging.getLogger(__name__)


def _add_transcripts(cfg: Config, reels: list[Reel]) -> None:
    if not (cfg.sources.transcribe and transcribe.available()):
        return
    cookies = str(cfg.path(cfg.sources.cookies_file)) if cfg.sources.cookies_file else ""
    for r in reels:
        if not r.transcript:
            r.transcript = transcribe.transcribe(
                media_url=r.media_url, page_url=r.url, model_size=cfg.sources.whisper_model, cookies_file=cookies
            )


def run(cfg: Config, db: DB) -> int:
    reels: list[Reel] = []
    for name, fetcher in (("apify", lambda: apify.fetch(cfg)), ("inbox", lambda: inbox.fetch(cfg, db))):
        try:
            got = fetcher()
            reels.extend(got)
        except Exception as e:
            log.exception("Source %s failed", name)
            db.log("ingest", f"Source {name} failed: {e}", "error")
    fresh = [r for r in reels if db.one("SELECT 1 FROM reels WHERE id = ?", (r.id,)) is None]
    _add_transcripts(cfg, fresh)
    added = save_reels(db, fresh)
    db.log("ingest", f"Fetched {len(reels)} reels, {added} new")
    return added
