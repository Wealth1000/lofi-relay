import asyncio
import logging
import re
import time
import urllib.error
import urllib.request

from app.config import (
    THUMBNAIL_MAX_BYTES,
    THUMBNAIL_TIMEOUT,
    THUMBNAIL_TTL,
)

log = logging.getLogger(__name__)

# Matches both configured URL shapes -- /watch?v=<id> and /live/<id>. A YouTube
# video ID is 11 characters of its URL-safe alphabet.
_VIDEO_ID = re.compile(
    r"(?:/live/|/embed/|/shorts/|[?&]v=|youtu\.be/)([\w-]{11})",
)

# i.ytimg.com serves artwork straight from the video ID, so a thumbnail needs no
# yt-dlp extraction. That matters: extraction is slow and globally serialized, so
# routing artwork through it would make a cover image wait behind stream resolves.
_THUMBNAIL_URL = "https://i.ytimg.com/vi/{video_id}/{variant}.jpg"

# Tried in order. maxresdefault is 1280x720 but YouTube does not generate it for
# every video; hqdefault always exists, so it is the guaranteed fallback.
_VARIANTS = ("maxresdefault", "hqdefault")

# source URL -> (JPEG bytes, monotonic expiry)
_cache: dict[str, tuple[bytes, float]] = {}

# Collapses a burst of concurrent first-time fetches for one stream into a single
# upstream request, rather than one per listener.
_fetch_lock = asyncio.Lock()


def video_id(url: str) -> str | None:
    """Extract the YouTube video ID from a watch or live URL."""
    match = _VIDEO_ID.search(url)

    if match is None:
        return None

    return match.group(1)


def get_thumbnail(url: str) -> bytes:
    """Fetch a stream's YouTube thumbnail as JPEG bytes.

    Blocking. Prefer resolve_thumbnail() from async code.
    """
    identifier = video_id(url)

    if identifier is None:
        raise RuntimeError(f"No YouTube video ID in {url}")

    for variant in _VARIANTS:
        thumbnail_url = _THUMBNAIL_URL.format(
            video_id=identifier,
            variant=variant,
        )

        try:
            with urllib.request.urlopen(
                thumbnail_url,
                timeout=THUMBNAIL_TIMEOUT,
            ) as response:
                # Read one byte past the cap so an oversized body is detected
                # rather than silently truncated into a corrupt JPEG.
                image = response.read(THUMBNAIL_MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # maxresdefault is simply absent for many videos, so a 404 here is
            # expected and only means the next variant should be tried.
            log.info(
                "Thumbnail %s unavailable for %s (HTTP %s)",
                variant,
                identifier,
                exc.code,
            )
            continue
        except urllib.error.URLError as exc:
            log.warning(
                "Thumbnail %s fetch failed for %s: %s",
                variant,
                identifier,
                exc.reason,
            )
            continue

        if len(image) > THUMBNAIL_MAX_BYTES:
            log.warning(
                "Thumbnail %s for %s exceeds %d bytes; trying next variant",
                variant,
                identifier,
                THUMBNAIL_MAX_BYTES,
            )
            continue

        return image

    raise RuntimeError(f"No thumbnail available for {identifier}")


def _cached(url: str) -> bytes | None:
    entry = _cache.get(url)

    if entry is None:
        return None

    image, expires_at = entry

    if expires_at <= time.monotonic():
        return None

    return image


def invalidate(url: str) -> None:
    """Drop a cached thumbnail so the next resolve re-fetches."""
    _cache.pop(url, None)


async def resolve_thumbnail(
    url: str,
    *,
    force: bool = False,
) -> bytes:
    """Fetch a stream's thumbnail, reusing a recent result when possible.

    Runs the blocking fetch in a worker thread for the same reason extraction
    does: a stalled event loop starves every in-flight relay, and a relay that
    falls behind the HLS live edge dies.
    """
    if force:
        invalidate(url)
    else:
        cached = _cached(url)

        if cached is not None:
            return cached

    async with _fetch_lock:
        # Another request may have fetched this while we waited for the lock.
        cached = _cached(url)

        if cached is not None:
            return cached

        log.info("Fetching thumbnail: %s", url)

        image = await asyncio.to_thread(get_thumbnail, url)

        log.info("Fetched thumbnail for %s (%d bytes)", url, len(image))

        _cache[url] = (image, time.monotonic() + THUMBNAIL_TTL)

        return image
