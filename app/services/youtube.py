import shutil

import yt_dlp

from app.config import YOUTUBE_COOKIES_PATH


def get_stream_url(url: str, format_id: str) -> str:
    runtime_cookie_file = "/tmp/youtube-cookies.txt"

    shutil.copyfile(
        YOUTUBE_COOKIES_PATH,
        runtime_cookie_file,
    )

    options = {
        "format": format_id,
        "quiet": False,
        "no_warnings": False,
        "cachedir": False,
        "cookiefile": runtime_cookie_file,
        "remote_components": ["ejs:github"],
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

        if "url" not in info:
            raise RuntimeError("yt-dlp did not return a stream URL")

        return info["url"]
