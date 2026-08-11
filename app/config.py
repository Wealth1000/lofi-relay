import os

UPSTREAM_FORMAT = "91"

YOUTUBE_COOKIES_PATH = os.getenv(
    "YOUTUBE_COOKIES_PATH",
    "/run/secrets/youtube-cookies.txt",
)

STREAMS = {
    "relax-study": "https://www.youtube.com/watch?v=X4VbdwhkE10",
    "chill-game": "https://www.youtube.com/live/4xDzrJKXOOY",
    "asian-relax-study": "https://www.youtube.com/live/1Tl2FtV06qo",
    "jazz-chill-study": "https://www.youtube.com/live/E2vONfzoyRI",
    "sleep-dream": "https://www.youtube.com/live/VAlMDl00mYY",
    "rain-sad": "https://www.youtube.com/live/CwPCy1GLS38",
    "piano-focus": "https://www.youtube.com/live/N0snMcR6aaA",
}
