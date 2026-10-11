"""Optional speech-to-text for reels. Most reels say the actual method out loud, not in the caption.

Uses faster-whisper locally (free, CPU is fine for short clips). Everything here degrades to
returning an empty string when the optional dependencies are missing.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _model(size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(size, device="cpu", compute_type="int8")


def _download_direct(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as fh:
        total = 0
        while chunk := resp.read(1 << 16):
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError("media too large")
            fh.write(chunk)
    return dest


def _download_page(url: str, workdir: Path, cookies_file: str = "") -> Path:
    import yt_dlp

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "media.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_DOWNLOAD_BYTES,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = list(workdir.glob("media.*"))
    if not files:
        raise RuntimeError("yt-dlp produced no file")
    return files[0]


def transcribe(media_url: str = "", page_url: str = "", model_size: str = "base", cookies_file: str = "") -> str:
    """Transcribe a reel from a direct media URL or (via yt-dlp) its page URL."""
    if not available() or not (media_url or page_url):
        return ""
    workdir = Path(tempfile.mkdtemp(prefix="mosaico-"))
    try:
        if media_url:
            path = _download_direct(media_url, workdir / "media.mp4")
        else:
            path = _download_page(page_url, workdir, cookies_file)
        segments, _info = _model(model_size).transcribe(str(path), vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:  # network errors, private reels, codec issues
        log.warning("Transcription failed for %s: %s", media_url or page_url, e)
        return ""
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
