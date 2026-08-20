import os

UPSTREAM_FORMAT = "91"

YOUTUBE_COOKIES_PATH = os.getenv(
    "YOUTUBE_COOKIES_PATH",
    "/etc/secrets/youtube-cookies.txt",
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# How long a resolved YouTube manifest URL may be reused. The URLs carry their
# own `expire` parameter (usually several hours); this only bounds how stale a
# cached one can get before we re-extract.
STREAM_URL_TTL = float(os.getenv("STREAM_URL_TTL", "3600"))

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
