import asyncio
import logging
import os
import time

import yt_dlp

from app.config import STREAM_URL_TTL
from app.services import jar_store

log = logging.getLogger(__name__)

# yt-dlp refreshes cookies into this writable copy (the Gist is remote and
# read-only here). Seeded once per container from the Gist, never re-copied:
# replaying the Gist on every extraction would present stale session tokens
# until the next scheduled browser refresh lands.
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

# Extraction is serialized globally, for two reasons: every call touches the
# same RUNTIME_COOKIE_FILE (concurrent calls would corrupt the cookie jar), and
# yt-dlp is expensive enough that running several at once on a small instance
# starves everything else.
_extract_lock = asyncio.Lock()


def seed_cookie_file() -> None:
    """Populate RUNTIME_COOKIE_FILE once per container.

    The Gist is the single source of truth for the cookie jar. If it is
    unconfigured, unreachable, or does not hold a usable jar, this fails loud
    at startup rather than silently falling back to a stale baked cookie --
    a dead jar would otherwise surface as a 503 on the first stream request.
    """
    if os.path.exists(RUNTIME_COOKIE_FILE):
        return

    if not jar_store.configured():
        raise RuntimeError(
            "Cookie jar unavailable: JAR_GIST_ID and JAR_GITHUB_TOKEN are not "
            "set. Seed the Gist (yt-cookies.txt) or configure the gist store."
        )

    remote = jar_store.pull()

    if remote is None:
        raise RuntimeError(
            "Cookie jar unavailable: the Gist is configured but returned no "
            "usable jar. Seed yt-cookies.txt in the Gist or check the token."
        )

    log.info("Seeding cookie jar from gist store")

    with open(RUNTIME_COOKIE_FILE, "w") as fh:
        fh.write(remote)


def _sync_jar() -> None:
    """Intentionally a no-op.

    The scheduled playwright-gist-updater job is the sole writer to the Gist.
    Pushing from here too would race it: both would PATCH the same file, and a
    lost race would discard either side's refresh for no benefit. The job
    runs on a fixed cadence that bounds staleness better than an unbounded
    push-per-extraction ever could, so the relay stays read-only on the Gist.
    """
    return


def get_stream_url(url: str, format_id: str) -> str:
    """Resolve a livestream URL to a playable manifest URL.

    Blocking. Prefer resolve_stream_url() from async code.
    """
    seed_cookie_file()

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
