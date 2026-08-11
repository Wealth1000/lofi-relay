import yt_dlp


def get_stream_url(url: str, format_id: str) -> str:
    options = {
        "format": format_id,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

        if "url" not in info:
            raise RuntimeError("yt-dlp did not return a stream URL")

        return info["url"]
