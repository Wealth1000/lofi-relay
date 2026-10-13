"""Resolve every configured YouTube source to a playable, audio-only m3u8.

Runs standalone on a GitHub Actions runner (and locally) without the FastAPI
stack: it imports only app.config (the stream map, labels and env-driven
constants) and the cookie-jar store, neither of which pulls in the relay.

Why standalone: the runner does not build or boot the relay image. It only
needs to extract, publish and commit. Keeping the dependency surface at the
stream map + jar store means a relay refactor can never break the resolver,
and vice versa.

The result is published as data/streams.json and committed by the workflow.
The document is merge-preserved: a failed refresh never blanks a previously
good entry, and a session-level rejection (dead jar, flagged runner IP)
defers the whole run instead of clobbering what is currently playing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError

from app.config import LOG_LEVEL, RATE_LIMIT_COOLDOWN, STREAMS, STREAM_LABELS
from app.services import jar_store
from app.services.thumbnail import video_id as extract_video_id

log = logging.getLogger(__name__)

DOCUMENT_PATH = "data/streams.json"

# Audio-only HLS rungs first. Verified live 2026-10-13: the mweb ladder of a
# live stream carries itag 234 (high) / 233 (low) as audio-only m3u8 variants,
# and their segments are pure AAC with no video track. bestaudio, then best,
# keep a channel resolvable even if it ever stops serving the audio-only rung;
# the rung actually chosen is recorded per entry so a muxed fallback (video
# bytes on the wire) is visible in the document rather than silent.
FORMAT = "234/233/bestaudio/best"

# Client strategies, tried in order per stream. The default (web) client is
# the one the audio-only live rungs were verified against; the relay's pinned
# mweb pair is the fallback, mirroring app/services/youtube.py.
CLIENTS: tuple[tuple[str, ...] | None, ...] = (None, ("mweb", "web_embedded"))

# One extraction attempt that runs longer than this is treated as a
# per-stream failure rather than letting one channel stall the whole batch.
ATTEMPT_TIMEOUT = 60

# Keep in sync with app/services/youtube.py: the same YouTube error text means
# the same state there and here. Login markers = the session (or this runner's
# IP) is refused outright, so stop the run and preserve the last good
# document. Rate-limit markers = the shared IP budget is throttled, so cool
# down once and defer the rest of the run to the next cron tick, which uses a
# fresh runner IP.
_LOGIN_MARKERS = (
    "sign in to confirm",
    "not a bot",
    "please sign in",
    "login required",
    "confirm your age",
)
_RATE_LIMIT_MARKERS = (
    "http error 429",
    "http error 403",
    "too many requests",
    "rate limit",
    "quota exceeded",
)

_impersonation_known = False
_impersonation_target: ImpersonateTarget | None = None


def _impersonate_target() -> ImpersonateTarget | None:
    """Return a Chrome impersonation target, or None when curl_cffi is unusable.

    Copied from app/services/youtube.py so the runner resolves exactly the way
    the relay does: without browser impersonation yt-dlp sends a stock Python
    fingerprint, which is what YouTube scores hardest and the usual trigger
    for a 403 on the player API. Probed once, because yt-dlp raises rather
    than falling back when the target has no handler.
    """
    global _impersonation_known, _impersonation_target

    if not _impersonation_known:
        candidate = ImpersonateTarget.from_str("chrome")

        try:
            yt_dlp.YoutubeDL({"impersonate": candidate})
        except Exception:
            log.error(
                "Browser impersonation unavailable; YouTube requests will use a "
                "default fingerprint. Install a supported curl_cffi build."
            )
        else:
            _impersonation_target = candidate

        _impersonation_known = True

    return _impersonation_target


class _YDLCapture:
    """Routes yt-dlp's message stream into this module's logger.

    Supplied as the `logger` param, which takes precedence over stderr. That
    redirect is the point: yt-dlp reports the HTTP status behind a failure as
    a warning and then raises an exception that omits it, so the warning
    stream is the only place a 429/403 survives long enough to act on.
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, message: str) -> None:
        log.debug(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)
        log.warning(message)

    def error(self, message: str) -> None:
        log.error(message)


@contextmanager
def _timeout(seconds: int, what: str):
    """POSIX per-attempt watchdog.

    SIGALRM's default action kills the process, so a handler that raises
    must be installed first; the previous handler is restored on exit. A
    blocked C call (a socket read) delays the handler until it returns, but
    yt-dlp's own socket timeouts bound that, so the watchdog still catches
    the pathological case. Runs here only on Linux (the Actions runner and
    this dev box); on a non-POSIX platform the alarm simply never fires.
    """
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _raise(signum, frame) -> None:
        raise TimeoutError(f"{what} timed out after {seconds}s")

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _seed_cookie_file() -> str | None:
    """Write the Gist's cookie jar to a temp file, or None to run jar-less.

    The jar is armor, not a gate: public lofi channels resolve without it
    (verified 2026-10-13), so an unconfigured or unreachable Gist degrades to
    jar-less extraction rather than failing the run. The Gist is the single
    source of truth and is re-pulled on every run, so each batch starts from
    the freshest session the updater has produced.
    """
    jar = jar_store.pull()

    if not jar:
        log.info("No cookie jar available; resolving without cookies")
        return None

    fd, path = tempfile.mkstemp(prefix="yt-cookies-", suffix=".txt")

    with os.fdopen(fd, "w") as fh:
        fh.write(jar)

    os.chmod(path, 0o600)
    log.info("Seeded cookie jar for resolution run")
    return path


def _classify(message: str, warnings: list[str]) -> str | None:
    """Classify a failed extraction: 'login', 'rate-limit', or None (ordinary).

    Matched case-insensitively against the raised message *and* the captured
    warnings, for the same reason youtube.py does: yt-dlp folds the status
    into warnings and then raises a bare "Failed to extract any player
    response" that carries no status code, so the exception alone can never
    tell a rate limit apart.
    """
    text = "\n".join((message, *warnings)).lower()

    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return "rate-limit"

    if any(marker in text for marker in _LOGIN_MARKERS):
        return "login"

    return None


def _resolve_one(source: str, cookie_file: str | None) -> tuple[str, dict | None, str]:
    """Attempt one source, trying each client strategy in turn.

    Returns (kind, fields, detail) where kind is one of 'resolved',
    'login', 'rate-limit' or 'failed'. 'login' and 'rate-limit' stop
    immediately: both are properties of the shared egress IP / session, not
    of this source, so further probing only extends the block or hastens a
    lockout.
    """
    last_detail = "extraction failed"

    for client in CLIENTS:
        captured = _YDLCapture()
        options: dict = {
            "format": FORMAT,
            "noplaylist": True,
            "quiet": True,
            "cachedir": False,
            "logger": captured,
        }

        if cookie_file:
            options["cookiefile"] = cookie_file

        if client is not None:
            options["extractor_args"] = {"youtube": {"player_client": list(client)}}

        impersonation = _impersonate_target()

        if impersonation is not None:
            options["impersonate"] = impersonation

        try:
            with _timeout(ATTEMPT_TIMEOUT, f"resolving {source}"):
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(source, download=False)
        except TimeoutError:
            last_detail = f"timed out after {ATTEMPT_TIMEOUT}s"
            log.warning("%s: %s", source, last_detail)
            continue
        except DownloadError as exc:
            detail = str(exc).splitlines()[0] if str(exc) else "extraction failed"
            kind = _classify(detail, captured.warnings)

            if kind == "login":
                log.error("%s: session rejected (%s)", source, detail)
                return "login", None, detail
            if kind == "rate-limit":
                log.warning("%s: rate limited (%s)", source, detail)
                return "rate-limit", None, detail

            last_detail = detail
            log.warning("%s: %s; trying next client strategy", source, detail)
            continue

        if "url" not in info:
            last_detail = "yt-dlp did not return a stream URL"
            continue

        vcodec = info.get("vcodec", "unknown")
        variant = "audio-only" if vcodec in ("none", "audio-only") else "muxed-fallback"

        return (
            "resolved",
            {
                "url": info["url"],
                "variant": variant,
                "format_id": str(info.get("format_id", "")),
            },
            "",
        )

    return "failed", None, last_detail


def _load_previous(path: str) -> dict:
    """Load the last published document, or an empty one when there is none."""
    try:
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
    except (OSError, ValueError):
        log.info("No usable previous document; starting a fresh one")
        return {"streams": {}}

    if not isinstance(document, dict) or not isinstance(document.get("streams"), dict):
        log.warning("Previous document is malformed; starting a fresh one")
        return {"streams": {}}

    return document


def _previous_entry(document: dict, name: str) -> dict:
    entry = document.get("streams", {}).get(name)
    return entry if isinstance(entry, dict) else {}


def _fresh_entry(name: str, source: str, fields: dict) -> dict:
    return {
        "source": source,
        "video_id": extract_video_id(source),
        "label": STREAM_LABELS.get(name),
        "url": fields["url"],
        "variant": fields["variant"],
        "format_id": fields["format_id"],
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "stale": False,
        "error": None,
    }


def _preserved_entry(name: str, source: str, previous: dict, error: str) -> dict:
    """Keep the last known-good URL and mark it stale rather than blanking it.

    A signed m3u8 stays valid for hours (verified ~24h on 2026-10-13), so a
    preserved entry keeps the stream playable through a bad run; the next cron
    tick replaces it.
    """
    return {
        "source": source,
        "video_id": extract_video_id(source),
        "label": STREAM_LABELS.get(name),
        "url": previous.get("url"),
        "variant": previous.get("variant"),
        "format_id": previous.get("format_id"),
        "resolved_at": previous.get("resolved_at"),
        "stale": True,
        "error": error,
    }


def run(document_path: str, dry_run: bool = False) -> int:
    """Resolve every stream and publish the document. Returns a process code.

    Exit 0 when the document holds at least one playable URL (fresh or
    preserved); exit 1 when nothing is playable at all, so a red run is
    visible in the workflow UI rather than a silent blank document.
    """
    cookie_file = _seed_cookie_file()
    previous = _load_previous(document_path)

    streams: dict[str, dict] = {}
    last_error: str | None = None
    session_dead = False
    rate_limited_all = False
    cooled_down = False

    try:
        for name, source in STREAMS.items():
            if session_dead:
                streams[name] = _preserved_entry(
                    name, source, _previous_entry(previous, name),
                    f"deferred: {last_error}",
                )
                continue

            if rate_limited_all:
                streams[name] = _preserved_entry(
                    name, source, _previous_entry(previous, name),
                    "deferred: runner IP rate-limited; next run uses a fresh IP",
                )
                continue

            kind, fields, detail = _resolve_one(source, cookie_file)

            if kind == "resolved":
                streams[name] = _fresh_entry(name, source, fields)
                log.info("%s: resolved (%s)", name, fields["variant"])
            elif kind == "login":
                session_dead = True
                last_error = (
                    "YouTube rejected the session; "
                    f"a fresh cookie export is needed ({detail})"
                )
                streams[name] = _preserved_entry(
                    name, source, _previous_entry(previous, name), last_error
                )
            elif kind == "rate-limit":
                # Cool down once, then defer the rest of the batch: the 429
                # budget is keyed on this runner's egress IP, so retrying the
                # remaining streams now only extends the block.
                if not cooled_down:
                    cooled_down = True
                    log.info(
                        "Rate limited; cooling down for %.0fs", RATE_LIMIT_COOLDOWN
                    )
                    time.sleep(RATE_LIMIT_COOLDOWN)
                    kind, fields, detail = _resolve_one(source, cookie_file)

                    if kind == "resolved":
                        streams[name] = _fresh_entry(name, source, fields)
                        log.info("%s: resolved after cooldown", name)
                        continue

                    if kind == "login":
                        session_dead = True
                        last_error = (
                            "YouTube rejected the session; "
                            f"a fresh cookie export is needed ({detail})"
                        )
                        streams[name] = _preserved_entry(
                            name, source, _previous_entry(previous, name),
                            last_error,
                        )
                        continue

                rate_limited_all = True
                streams[name] = _preserved_entry(
                    name, source, _previous_entry(previous, name),
                    f"deferred: rate limited ({detail})",
                )
            else:
                streams[name] = _preserved_entry(
                    name, source, _previous_entry(previous, name),
                    detail,
                )
    finally:
        if cookie_file:
            try:
                os.unlink(cookie_file)
            except OSError:
                pass

    playable = any(
        entry.get("url") for entry in streams.values()
    )

    document = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if playable else "stale",
        "last_error": last_error,
        "streams": streams,
    }

    if dry_run:
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        os.makedirs(os.path.dirname(document_path) or ".", exist_ok=True)
        with open(document_path, "w", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, sort_keys=True)
            fh.write("\n")
        log.info(
            "Wrote %s (%d streams, %d playable)",
            document_path, len(streams), sum(1 for e in streams.values() if e.get("url")),
        )

    return 0 if playable else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document",
        default=DOCUMENT_PATH,
        help=f"Path of the published document (default: {DOCUMENT_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the document to stdout instead of writing it",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    return run(args.document, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
