import asyncio
import logging
import os
import shutil
import time

import yt_dlp

from app.config import JAR_PUSH_INTERVAL, STREAM_URL_TTL, YOUTUBE_COOKIES_PATH
from app.services import jar_store

log = logging.getLogger(__name__)

# yt-dlp refreshes cookies into this writable copy (the mounted secret is
# read-only on Render). Seeded once per container, never re-copied: replaying
# the original export on every extraction presents stale session tokens until
# YouTube kills the session.
RUNTIME_COOKIE_FILE = "/tmp/youtube-cookies.txt"

# yt-dlp error substrings that mean the session is dead, as opposed to a
# transient extraction failure worth retrying. Matched case-insensitively.
_LOGIN_MARKERS = (
    "sign in to confirm",
    "not a bot",
    "please sign in",
    "login required",
    "confirm your age",
)


class ReloginRequiredError(RuntimeError):
    """YouTube rejected the cookie jar; a fresh export is needed."""


# source URL -> (resolved manifest URL, monotonic expiry)
_cache: dict[str, tuple[str, float]] = {}

# Jar content currently stored in the gist, when known. Used to skip pushes
# that would change nothing.
_synced_jar: str | None = None
_last_jar_push = 0.0

# Extraction is serialized globally, for two reasons: every call touches the
# same RUNTIME_COOKIE_FILE (concurrent calls would corrupt the cookie jar), and
# yt-dlp is expensive enough that running several at once on a small instance
# starves everything else.
_extract_lock = asyncio.Lock()


def _seed_cookie_file() -> None:
    """Populate RUNTIME_COOKIE_FILE once per container.

    Preference order: the jar refreshed by a previous container (pulled from
    the gist store, survives cold boots), then the mounted secret export
    (fresh as of the last deploy).
    """
    global _synced_jar

    if os.path.exists(RUNTIME_COOKIE_FILE):
        return

    remote = jar_store.pull()

    if remote is not None:
        log.info("Seeding cookie jar from gist store")
        _synced_jar = remote

        with open(RUNTIME_COOKIE_FILE, "w") as fh:
            fh.write(remote)

        return

    log.info("Seeding cookie jar from mounted secret")
    shutil.copyfile(
        YOUTUBE_COOKIES_PATH,
        RUNTIME_COOKIE_FILE,
    )


def _sync_jar() -> None:
    """Push the refreshed jar to the gist store when it changed.

    Throttled by JAR_PUSH_INTERVAL: the point of failure this guards against
    is a cold boot, so a jar a few minutes stale on the gist is fine.
    """
    global _synced_jar, _last_jar_push

    if not jar_store.configured():
        return

    try:
        with open(RUNTIME_COOKIE_FILE) as fh:
            content = fh.read()
    except OSError:
        log.exception("Failed to read runtime cookie jar for sync")
        return

    if content == _synced_jar:
        return

    now = time.monotonic()

    if now - _last_jar_push < JAR_PUSH_INTERVAL:
        return

    if jar_store.push(content):
        _synced_jar = content
        _last_jar_push = now


def get_stream_url(url: str, format_id: str) -> str:
    """Resolve a livestream URL to a playable manifest URL.

    Blocking. Prefer resolve_stream_url() from async code.
    """
    _seed_cookie_file()

    options = {
        "format": format_id,
        "quiet": True,
        "no_warnings": False,
        "cachedir": False,
        "cookiefile": RUNTIME_COOKIE_FILE,
        "remote_components": ["ejs:github"],
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc).lower()

        if any(marker in message for marker in _LOGIN_MARKERS):
            raise ReloginRequiredError(
                "YouTube rejected the cookie jar; a fresh export is needed"
            ) from exc

        raise

    if "url" not in info:
        raise RuntimeError("yt-dlp did not return a stream URL")

    _sync_jar()

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
