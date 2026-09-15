import json
import logging
import urllib.error
import urllib.request

from app.config import JAR_GIST_ID, JAR_GITHUB_TOKEN, JAR_TIMEOUT

log = logging.getLogger(__name__)

GIST_API = "https://api.github.com/gists"
GIST_FILENAME = "yt-cookies.txt"


def configured() -> bool:
    return bool(JAR_GIST_ID and JAR_GITHUB_TOKEN)


def _request(method: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None

    request = urllib.request.Request(
        f"{GIST_API}/{JAR_GIST_ID}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {JAR_GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            # The GitHub API rejects requests without a User-Agent.
            "User-Agent": "lofi-relay",
        },
    )

    with urllib.request.urlopen(request, timeout=JAR_TIMEOUT) as response:
        return json.loads(response.read().decode())


def _looks_like_jar(content: str | None) -> bool:
    """Distinguish a real cookie jar from a placeholder or an error page.

    Netscape cookie format is tab-separated with 7 fields per record; the
    body of a gist holding anything else will not have that shape.
    """
    if not content:
        return False

    return any(
        line.count("\t") >= 6 and ".youtube.com" in line
        for line in content.splitlines()
        if line and not line.startswith("#")
    )


def pull() -> str | None:
    """Fetch the refreshed jar from the gist, or None when unusable.

    Never raises: the store is an optimization for cold boots, and extraction
    must fall back to the mounted secret either way.
    """
    if not configured():
        return None

    try:
        gist = _request("GET")
        entry = gist.get("files", {}).get(GIST_FILENAME)

        if entry is None:
            log.warning("Gist %s has no %s file", JAR_GIST_ID, GIST_FILENAME)
            return None

        content = entry.get("content")

        if not _looks_like_jar(content):
            log.info("Gist jar is not a valid cookie jar; ignoring it")
            return None

        return content
    except (urllib.error.URLError, OSError, ValueError):
        log.exception("Failed to pull cookie jar from gist")
        return None


def push(content: str) -> bool:
    """Store the refreshed jar in the gist. Never raises."""
    if not configured():
        return False

    try:
        _request("PATCH", {"files": {GIST_FILENAME: {"content": content}}})
        log.info("Pushed refreshed cookie jar to gist")
        return True
    except (urllib.error.URLError, OSError, ValueError):
        log.exception("Failed to push cookie jar to gist")
        return False
