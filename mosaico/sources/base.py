"""Common reel shape and persistence for all sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..db import DB, now


@dataclass
class Reel:
    id: str
    source: str
    url: str = ""
    author: str = ""
    caption: str = ""
    transcript: str = ""
    views: int = 0
    likes: int = 0
    comments: int = 0
    posted_at: str = ""
    media_url: str = ""  # direct video URL when the source provides one (used for transcription)


def too_old(posted_at: str, max_age_days: int) -> bool:
    if not posted_at or max_age_days <= 0:
        return False
    try:
        ts = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > timedelta(days=max_age_days)


def save_reels(db: DB, reels: list[Reel]) -> int:
    """Insert reels that are new. Returns how many were added."""
    added = 0
    for r in reels:
        if not (r.caption or r.transcript):
            continue
        added += bool(db.insert("reels", {
            "id": r.id,
            "source": r.source,
            "url": r.url,
            "author": r.author,
            "caption": r.caption,
            "transcript": r.transcript,
            "views": int(r.views or 0),
            "likes": int(r.likes or 0),
            "comments": int(r.comments or 0),
            "posted_at": r.posted_at,
            "fetched_at": now(),
        }, or_ignore=True))
    return added
