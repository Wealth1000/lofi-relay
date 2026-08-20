import asyncio
import logging
import shutil
import time

import yt_dlp

from app.config import STREAM_URL_TTL, YOUTUBE_COOKIES_PATH

log = logging.getLogger(__name__)

# yt-dlp writes updated cookies back to its cookiefile, so the secret mount
# (read-only on Render) is copied to a writable location first.
RUNTIME_COOKIE_FILE = "/tmp/youtube-cookies.txt"

# source URL -> (resolved manifest URL, monotonic expiry)
_cache: dict[str, tuple[str, float]] = {}

# Extraction is serialized globally, for two reasons: every call writes the same
# RUNTIME_COOKIE_FILE (concurrent calls would corrupt the cookie jar), and
# yt-dlp is expensive enough that running several at once on a small instance
# starves everything else.
_extract_lock = asyncio.Lock()


def get_stream_url(url: str, format_id: str) -> str:
    """Resolve a livestream URL to a playable manifest URL.

    Blocking. Prefer resolve_stream_url() from async code.
    """
    shutil.copyfile(
        YOUTUBE_COOKIES_PATH,
        RUNTIME_COOKIE_FILE,
    )

    options = {
        "format": format_id,
        "quiet": True,
        "no_warnings": False,
        "cachedir": False,
        "cookiefile": RUNTIME_COOKIE_FILE,
        "remote_components": ["ejs:github"],
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

        if "url" not in info:
            raise RuntimeError("yt-dlp did not return a stream URL")

        return info["url"]


def _cached(url: str) -> str | None:
    entry = _cache.get(url)

    if entry is None:
        return None

    stream_url, expires_at = entry

    if expires_at <= time.monotonic():
        return None

    return stream_url


def invalidate(url: str) -> None:
    """Drop a cached manifest URL so the next resolve re-extracts."""
    _cache.pop(url, None)


async def resolve_stream_url(
    url: str,
    format_id: str,
    *,
    force: bool = False,
) -> str:
    """Resolve a livestream URL, reusing a recent result when possible.

    Runs the blocking yt-dlp extraction in a worker thread so it never stalls
    the event loop -- a stall there would starve every in-flight relay, which
    then falls behind the HLS live edge and dies.
    """
    if force:
        invalidate(url)
    else:
        cached = _cached(url)

        if cached is not None:
            return cached

    async with _extract_lock:
        # Another request may have resolved this while we waited for the lock.
        cached = _cached(url)

        if cached is not None:
            return cached

        log.info("Resolving stream URL: %s", url)
        started = time.monotonic()

        stream_url = await asyncio.to_thread(get_stream_url, url, format_id)

        log.info(
            "Resolved %s in %.1fs",
            url,
            time.monotonic() - started,
        )

        _cache[url] = (stream_url, time.monotonic() + STREAM_URL_TTL)

        return stream_url
