import yt_dlp

from app.config import YOUTUBE_COOKIES_PATH


def get_stream_url(url: str, format_id: str) -> str:
    options = {
        "format": format_id,
        "quiet": True,
        "no_warnings": False,
        "cookiefile": YOUTUBE_COOKIES_PATH,
        "remote_components": ["ejs:github"],
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

        if "url" not in info:
            raise RuntimeError("yt-dlp did not return a stream URL")

        return info["url"]

