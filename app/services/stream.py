import asyncio
from collections.abc import AsyncGenerator


async def audio_stream(url: str) -> AsyncGenerator[bytes, None]:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",

        "-i", url,

        "-vn",
        "-c:a", "copy",

        "-f", "adts",
        "pipe:1",

        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        while True:
            chunk = await process.stdout.read(64 * 1024)

            if not chunk:
                break

            yield chunk

    finally:
        if process.returncode is None:
            process.kill()

        await process.wait()
