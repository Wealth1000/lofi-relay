import os

UPSTREAM_FORMAT = "91"

# Remote store for the cookie jar (a private GitHub Gist). This is the single
# source of truth: the jar is seeded from it at cold boot, refreshed by
# yt-dlp at runtime, and kept fresh by the scheduled playwright-gist-updater
# job. YouTube rotates session cookies, so the jar must survive across
# extractions AND across container restarts -- the free tier's ephemeral
# filesystem would otherwise throw the refreshes away on every cold boot.
# Set both to enable; leave both unset to run jar-less (extraction then has no
# cookies and most streams will require a signed-in session).
JAR_GIST_ID = os.getenv("JAR_GIST_ID")
JAR_GITHUB_TOKEN = os.getenv("JAR_GITHUB_TOKEN")

# Timeout for gist API calls. The store is a dependency at cold boot (the jar
# is seeded from it), but a hung call must not stall extraction indefinitely.
JAR_TIMEOUT = float(os.getenv("JAR_TIMEOUT", "15"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# How long a resolved YouTube manifest URL may be reused. The URLs carry their
# own `expire` parameter (usually several hours); this only bounds how stale a
# cached one can get before we re-extract.
STREAM_URL_TTL = float(os.getenv("STREAM_URL_TTL", "3600"))

# Thumbnail artwork, served so clients can show cover art in their media UI.
# Thumbnail URLs are derived from the video ID, so serving one never involves
# yt-dlp -- artwork must not queue behind the slow, serialized extraction path.
THUMBNAIL_TTL = float(os.getenv("THUMBNAIL_TTL", "21600"))
THUMBNAIL_TIMEOUT = float(os.getenv("THUMBNAIL_TIMEOUT", "15"))

# Sanity bound on remote image data buffered in memory. Thumbnails run 100-200KB.
THUMBNAIL_MAX_BYTES = int(
    os.getenv("THUMBNAIL_MAX_BYTES", str(4 * 1024 * 1024)),
)

# FFmpeg supervision. A livestream should never end, so an FFmpeg exit is
# treated as a fault to recover from rather than end-of-stream.
FFMPEG_MAX_RESTARTS = int(os.getenv("FFMPEG_MAX_RESTARTS", "5"))
FFMPEG_RESTART_DELAY = float(os.getenv("FFMPEG_RESTART_DELAY", "2"))
FFMPEG_RESTART_DELAY_MAX = float(os.getenv("FFMPEG_RESTART_DELAY_MAX", "30"))

STREAMS = {
    "relax-study": "https://www.youtube.com/watch?v=rFZHOHl-L8A",
    "chill-game": "https://www.youtube.com/live/4xDzrJKXOOY",
    "asian-relax-study": "https://www.youtube.com/live/1Tl2FtV06qo",
    "jazz-chill-study": "https://www.youtube.com/live/E2vONfzoyRI",
    "sleep-dream": "https://www.youtube.com/live/VAlMDl00mYY",
    "rain-sad": "https://www.youtube.com/live/CwPCy1GLS38",
    "piano-focus": "https://www.youtube.com/live/N0snMcR6aaA",
}
