import logging

import yt_dlp

logger = logging.getLogger(__name__)


def get_stream_url(url: str, format_id: str) -> str:
    options = {
        "format": format_id,
        "quiet": True,
        "no_warnings": False,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

            if "url" not in info:
                raise RuntimeError("yt-dlp did not return a stream URL")

            return info["url"]

    except Exception:
        logger.exception("yt-dlp failed while extracting stream: %s", url)
        raise
