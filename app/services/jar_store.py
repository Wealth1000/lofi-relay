import json
import logging
import re
import urllib.error
import urllib.request

from app.config import JAR_GITHUB_TOKEN, JAR_GIST_ID, JAR_TIMEOUT

# http.cookiejar uses this regex to decide whether a file is a Netscape
# cookie jar. Reusing it here keeps the relay's validator in lockstep with
# the exact rule yt-dlp enforces at extraction time.
NETSCAPE_MAGIC_RGX = re.compile(r"#(?: Netscape)? HTTP Cookie File")

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

    http.cookiejar's Netscape loader is stricter than "has 7 tab-separated
    fields". It requires the magic header line, and it asserts that the
    domain_specified flag matches whether the domain starts with ".". A jar
    that fails either check is rejected by yt-dlp with
    "does not look like a Netscape format cookies file", which surfaces as a
    502 on the first stream. This mirrors those rules so a bad jar is caught
    at cold boot instead of at extraction time.

    The expires field (index 4) must also be a non-negative integer: yt-dlp's
    loader rejects negative values outright and then treats the whole file as
    not-Netscape-format, so a jar of session cookies written as -1 is not
    usable.
    """
    if not content:
        return False

    lines = content.splitlines()
    if not lines or not NETSCAPE_MAGIC_RGX.match(lines[0]):
        return False

    for line in lines[1:]:
        if not line or line.startswith("#"):
            continue

        fields = line.split("\t")
        if len(fields) < 7 or ".youtube.com" not in fields[0]:
            continue

        # domain_specified flag must agree with the leading dot.
        if (fields[1] == "TRUE") != fields[0].startswith("."):
            return False

        try:
            expires = int(fields[4])
        except ValueError:
            return False

        if expires < 0:
            return False

        return True

    return False


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
