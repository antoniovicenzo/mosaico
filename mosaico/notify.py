"""Optional push notifications through a Slack- or Discord-compatible incoming webhook."""

from __future__ import annotations

import json
import logging
import urllib.request

from .config import Config

log = logging.getLogger(__name__)


def send(cfg: Config, text: str) -> bool:
    url = cfg.webhook_url
    if not url:
        return False
    body = json.dumps({"text": text, "content": text[:1900]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as e:
        log.warning("Notification failed: %s", e)
        return False
