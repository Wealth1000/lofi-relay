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

# How long to stop calling YouTube after it rate-limits this server's egress
# IP. The 429 budget is keyed per IP and its timer restarts on every attempt,
# so retrying during the window extends the block for every stream at once.
RATE_LIMIT_COOLDOWN = float(os.getenv("RATE_LIMIT_COOLDOWN", "120"))

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
    "lofi-house": "https://www.youtube.com/watch?v=3PFJ9SETS4M",
    "relax-study": "https://www.youtube.com/watch?v=rFZHOHl-L8A",
    "summer-lofi": "https://www.youtube.com/watch?v=0muHFBSiybw",
    "sleep-dream": "https://www.youtube.com/watch?v=VAlMDl00mYY",
    "synth-ambient": "https://www.youtube.com/watch?v=GSfT7H87zq4",
    "study-with-me": "https://www.youtube.com/watch?v=qGohtGC5Rtk",
    "sad-rainy": "https://www.youtube.com/watch?v=CwPCy1GLS38",
    "asian-relax-study": "https://www.youtube.com/watch?v=1Tl2FtV06qo",
    "jazz-chill-study": "https://www.youtube.com/watch?v=E2vONfzoyRI",
    "sleep-chill": "https://www.youtube.com/watch?v=JD-kMIpDfnY",
    "piano-focus": "https://www.youtube.com/watch?v=N0snMcR6aaA",
    "christmas-lofi": "https://www.youtube.com/watch?v=XSXEaikz0Bc",
    "classical-study": "https://www.youtube.com/watch?v=jXAEIWcGXwE",
    "relaxing-jazz": "https://www.youtube.com/watch?v=A8jDx9TLMQc",
    "halloween-lofi": "https://www.youtube.com/watch?v=3GQY80jyysQ",
    "fireplace-ambience": "https://www.youtube.com/watch?v=q_4KI-ChIIs",
    "chill-guitar": "https://www.youtube.com/watch?v=E_XmwjgRLz8",
    "sleep-ambient": "https://www.youtube.com/watch?v=xORCbIptqcc",
    "medieval-lofi": "https://www.youtube.com/watch?v=IxPANmjPaek",
    "gentle-rain": "https://www.youtube.com/watch?v=-OekvEFm1lo",
    "dark-ambient": "https://www.youtube.com/watch?v=S_MOd40zlYU",
    "synthwave": "https://www.youtube.com/watch?v=4xDzrJKXOOY",
}
