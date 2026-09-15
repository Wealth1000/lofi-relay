import os

UPSTREAM_FORMAT = "91"

YOUTUBE_COOKIES_PATH = os.getenv(
    "YOUTUBE_COOKIES_PATH",
    "/etc/secrets/youtube-cookies.txt",
)

# Remote store for the refreshed cookie jar (a private GitHub Gist). YouTube
# rotates session cookies and yt-dlp writes the refreshed values back to its
# cookiefile, so the jar must survive across extractions AND across container
# restarts -- the free tier's ephemeral filesystem would otherwise throw the
# refreshes away on every cold boot. Set both to enable; leave unset to run
# jar-less (extraction then uses only the mounted secret, as before).
JAR_GIST_ID = os.getenv("JAR_GIST_ID")
JAR_GITHUB_TOKEN = os.getenv("JAR_GITHUB_TOKEN")

# Minimum seconds between jar pushes to the gist. YouTube nudges cookies on
# many extractions, so pushing every change would hammer the gist for nothing:
# only cold boots ever read it back.
JAR_PUSH_INTERVAL = float(os.getenv("JAR_PUSH_INTERVAL", "600"))

# Timeout for gist API calls. The store is an optimization, never a
# dependency -- but a hung call must not stall extraction indefinitely.
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
