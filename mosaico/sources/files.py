"""Import reels from a JSON / JSONL / CSV file (e.g. an Apify dataset export or your own notes)."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from .apify import parse_item
from .base import Reel


def _from_dict(d: dict) -> Reel | None:
    reel = parse_item(d)
    if reel is not None:
        reel.source = "file"
        return reel
    caption = d.get("caption") or d.get("text") or d.get("description") or ""
    transcript = d.get("transcript") or ""
    if not (caption or transcript):
        return None
    url = d.get("url") or ""
    key = url or hashlib.sha1((caption + transcript).encode()).hexdigest()[:16]
    return Reel(
        id=f"file:{key}",
        source="file",
        url=url,
        author=d.get("author") or d.get("ownerUsername") or "",
        caption=caption,
        transcript=transcript,
        views=int(d.get("views") or 0),
        likes=int(d.get("likes") or 0),
        comments=int(d.get("comments") or 0),
        posted_at=d.get("posted_at") or d.get("timestamp") or "",
    )


def load(path: str | Path) -> list[Reel]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
    elif path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("items", [])
    return [r for r in (_from_dict(row) for row in rows) if r is not None]
