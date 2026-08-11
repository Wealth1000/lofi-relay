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
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        while True:
            chunk = await process.stdout.read(64 * 1024)

            if not chunk:
                break

            yield chunk

        stderr = await process.stderr.read()

        if process.returncode != 0:
            print(
                f"FFmpeg exited with code {process.returncode}: "
                f"{stderr.decode(errors='replace')}",
                flush=True,
            )

    finally:
        if process.returncode is None:
            process.kill()

        await process.wait()
