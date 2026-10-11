"""Reels you paste in by hand: one URL per line in the inbox file, fetched with yt-dlp.

This is the zero-cost, zero-risk path: you scroll Instagram as normal, share/copy the links of
reels that look interesting, and the agent picks them up on its next cycle.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..db import DB
from .base import Reel

log = logging.getLogger(__name__)

SHORTCODE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")


def reel_id(url: str) -> str:
    m = SHORTCODE.search(url)
    return f"ig:{m.group(1)}" if m else f"url:{url.strip()}"


def pending_urls(cfg: Config, db: DB) -> list[str]:
    path = cfg.path(cfg.sources.url_inbox)
    if not path.exists():
        return []
    urls = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if db.one("SELECT 1 FROM reels WHERE id = ?", (reel_id(line),)) is None:
            urls.append(line)
    return urls


def fetch_one(url: str, cookies_file: str = "") -> Reel:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    posted = ""
    if info.get("timestamp"):
        posted = datetime.fromtimestamp(info["timestamp"], tz=timezone.utc).isoformat()
    return Reel(
        id=reel_id(url),
        source="inbox",
        url=url,
        author=info.get("uploader") or info.get("channel") or "",
        caption=info.get("description") or info.get("title") or "",
        views=int(info.get("view_count") or 0),
        likes=int(info.get("like_count") or 0),
        comments=int(info.get("comment_count") or 0),
        posted_at=posted,
    )


def fetch(cfg: Config, db: DB) -> list[Reel]:
    urls = pending_urls(cfg, db)
    if not urls:
        return []
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        log.warning("%d inbox URLs waiting but yt-dlp is not installed (pip install 'mosaico[video]')", len(urls))
        return []
    cookies = str(cfg.path(cfg.sources.cookies_file)) if cfg.sources.cookies_file else ""
    reels = []
    for url in urls:
        try:
            reels.append(fetch_one(url, cookies))
        except Exception as e:
            log.warning("Could not fetch %s: %s", url, e)
            db.log("ingest", f"Inbox URL failed ({e.__class__.__name__}): {url}", "warn")
            # Remember the failure so we don't retry forever.
            db.insert("reels", {
                "id": reel_id(url), "source": "inbox", "url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "status": "error", "error": str(e)[:500],
            }, or_ignore=True)
    return reels


def append_urls(cfg: Config, urls: list[str]) -> Path:
    path = cfg.path(cfg.sources.url_inbox)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for u in urls:
            fh.write(u.strip() + "\n")
    return path
