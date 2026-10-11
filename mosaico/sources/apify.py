"""Instagram reels via an Apify actor.

Apify runs the scraper on its own infrastructure and handles Instagram's login walls and rate
limits, which is far more reliable (and keeps your personal account out of it) than scraping
from your own IP. The free Apify plan includes monthly credits that cover a few thousand reels.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from ..config import Config
from .base import Reel, too_old

log = logging.getLogger(__name__)

API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


def _first(item: dict, *keys, default=None):
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return default


def parse_item(item: dict) -> Reel | None:
    """Map one Apify Instagram item to a Reel. Tolerates the different actors' field names."""
    short = _first(item, "shortCode", "shortcode", "code", "id")
    if not short:
        return None
    is_video = item.get("type") in (None, "Video") or item.get("productType") == "clips" or item.get("isVideo")
    if not is_video:
        return None
    return Reel(
        id=f"ig:{short}",
        source="apify",
        url=_first(item, "url", "postUrl", default=f"https://www.instagram.com/reel/{short}/"),
        author=_first(item, "ownerUsername", "username", default=""),
        caption=_first(item, "caption", "text", default="") or "",
        transcript=_first(item, "transcript", default="") or "",
        views=int(_first(item, "videoPlayCount", "videoViewCount", "playsCount", "viewsCount", default=0) or 0),
        likes=int(_first(item, "likesCount", "likes", default=0) or 0),
        comments=int(_first(item, "commentsCount", "comments", default=0) or 0),
        posted_at=_first(item, "timestamp", "takenAt", default="") or "",
        media_url=_first(item, "videoUrl", default="") or "",
    )


def fetch(cfg: Config, hashtags: list[str] | None = None) -> list[Reel]:
    token = cfg.apify_token
    if not token:
        log.info("APIFY_TOKEN not set; skipping Instagram scrape")
        return []
    hashtags = hashtags or cfg.sources.hashtags
    payload = {
        "hashtags": hashtags,
        "resultsLimit": cfg.sources.reels_per_hashtag,
        "resultsType": "reels",
    }
    url = API.format(actor=cfg.sources.apify_actor) + "?" + urllib.parse.urlencode({"token": token, "timeout": 280})
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=320) as resp:
        items = json.loads(resp.read())
    reels = []
    for item in items:
        reel = parse_item(item)
        if reel is None:
            continue
        if cfg.sources.min_views and reel.views and reel.views < cfg.sources.min_views:
            continue
        if too_old(reel.posted_at, cfg.sources.max_age_days):
            continue
        reels.append(reel)
    log.info("Apify returned %d items, kept %d reels", len(items), len(reels))
    return reels
