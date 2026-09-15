import asyncio
import logging
import time
from collections.abc import AsyncGenerator

from app.config import (
    FFMPEG_MAX_RESTARTS,
    FFMPEG_RESTART_DELAY,
    FFMPEG_RESTART_DELAY_MAX,
)
from app.services.youtube import ReloginRequiredError, resolve_stream_url

log = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024

# Grace period for FFmpeg to exit on SIGTERM before it gets SIGKILL.
TERMINATE_TIMEOUT = 5.0

# An FFmpeg session that lasted this long is treated as healthy, so the restart
# counter resets. Without this, a stream that repeatedly dies just after
# starting would retry forever instead of eventually giving up.
HEALTHY_RUNTIME = 30.0

BASE_INPUT_ARGS = (
    "-nostdin",
    "-hide_banner",
    "-loglevel", "warning",
)

# Options belonging to the HTTP protocol, so they are only valid for http(s)
# inputs -- passing them for any other input makes FFmpeg refuse to start.
# Availability also varies by FFmpeg version (the deployment image ships a
# different build than a typical dev machine), so _optional_args_ok below
# disables them permanently if this build rejects them.
HTTP_INPUT_ARGS = (
    # Heal transient upstream drops in-process rather than exiting.
    "-reconnect", "1",
    "-reconnect_streamed", "1",
    "-reconnect_on_network_error", "1",
    "-reconnect_delay_max", "5",

    # Turn a silently wedged read into an exit the supervisor can act on.
    "-rw_timeout", "15000000",
)

FFMPEG_OUTPUT_ARGS = (
    "-vn",
    "-c:a", "copy",

    "-f", "adts",
    "pipe:1",
)

OPTION_ERROR_MARKERS = (
    "option not found",
    "unrecognized option",
    "error splitting the argument",
)

_optional_args_ok = True


def _input_args(url: str) -> tuple[str, ...]:
    if _optional_args_ok and url.lower().startswith(("http://", "https://")):
        return BASE_INPUT_ARGS + HTTP_INPUT_ARGS

    return BASE_INPUT_ARGS


async def _spawn(url: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "ffmpeg",
        *_input_args(url),
        "-i", url,
        *FFMPEG_OUTPUT_ARGS,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _log_stderr(
    process: asyncio.subprocess.Process,
    session: dict,
) -> None:
    """Drain FFmpeg's stderr continuously, logging as lines arrive.

    This has to run concurrently with the relay. stderr is a pipe with a small
    kernel buffer, so if it is only read after stdout closes, a long-running
    stream eventually fills it and FFmpeg blocks forever in write() -- the audio
    stops while the connection stays open and nothing reports an error.
    """
    try:
        while True:
            line = await process.stderr.readline()

            if not line:
                break

            text = line.decode(errors="replace").rstrip()
            lowered = text.lower()

            if any(marker in lowered for marker in OPTION_ERROR_MARKERS):
                session["option_error"] = True

            log.warning("ffmpeg: %s", text)
    except Exception:
        # Never let the drainer raise: it is fire-and-forget, and losing log
        # lines must not take down the stream.
        log.exception("stderr drain failed")


def _kill(process: asyncio.subprocess.Process) -> None:
    try:
        process.kill()
    except ProcessLookupError:
        pass


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    try:
        process.terminate()
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(process.wait(), TERMINATE_TIMEOUT)
    except asyncio.TimeoutError:
        _kill(process)
    except asyncio.CancelledError:
        _kill(process)
        raise


async def _backoff(failures: int) -> None:
    delay = min(
        FFMPEG_RESTART_DELAY * failures,
        FFMPEG_RESTART_DELAY_MAX,
    )

    log.info("Restarting relay in %.1fs", delay)

    await asyncio.sleep(delay)


async def audio_stream(
    source_url: str,
    format_id: str,
) -> AsyncGenerator[bytes, None]:
    """Relay a livestream's audio, surviving FFmpeg failures.

    A livestream should never end, so FFmpeg exiting is treated as a fault:
    the manifest URL is re-resolved and FFmpeg respawned within the same HTTP
    response, so the client sees one continuous stream rather than a disconnect.
    """
    global _optional_args_ok

    failures = 0
    force_resolve = False

    while True:
        try:
            url = await resolve_stream_url(
                source_url,
                format_id,
                force=force_resolve,
            )
        except ReloginRequiredError:
            # Respawning cannot fix dead credentials, and each attempt is
            # another authenticated request that hastens the next lockout.
            log.error(
                "Cookie jar rejected for %s; stopping relay "
                "(re-export cookies required)",
                source_url,
            )
            break
        except Exception:
            log.exception("Failed to resolve %s", source_url)

            failures += 1
            force_resolve = True

            if failures > FFMPEG_MAX_RESTARTS:
                break

            await _backoff(failures)
            continue

        session: dict = {"option_error": False}

        process = await _spawn(url)
        stderr_task = asyncio.create_task(_log_stderr(process, session))

        started = time.monotonic()
        relayed = 0

        try:
            while True:
                chunk = await process.stdout.read(CHUNK_SIZE)

                if not chunk:
                    break

                relayed += len(chunk)

                yield chunk
        finally:
            # Runs on client disconnect too, where GeneratorExit is thrown in
            # at the yield above. GeneratorExit derives from BaseException, so
            # it passes straight through the `except Exception` handlers here
            # and tears the relay down instead of triggering a respawn.
            try:
                await _terminate(process)
            finally:
                stderr_task.cancel()

        uptime = time.monotonic() - started

        # This FFmpeg build rejected one of the optional HTTP options. Drop
        # them and retry at once; the supervisor still covers recovery, we
        # just lose in-process reconnects.
        if session["option_error"] and _optional_args_ok and not relayed:
            log.warning(
                "FFmpeg rejected an optional HTTP option; "
                "retrying without in-process reconnect support"
            )
            _optional_args_ok = False
            continue

        if uptime >= HEALTHY_RUNTIME:
            failures = 0

        failures += 1

        # The manifest may have rotated or its `expire` lapsed, so re-extract
        # rather than respawning against a URL that is already dead.
        force_resolve = True

        log.warning(
            "FFmpeg exited with code %s after %.0fs / %d bytes "
            "(attempt %d of %d)",
            process.returncode,
            uptime,
            relayed,
            failures,
            FFMPEG_MAX_RESTARTS,
        )

        if failures > FFMPEG_MAX_RESTARTS:
            break

        await _backoff(failures)

    log.error(
        "Giving up on %s after %d consecutive failures",
        source_url,
        failures,
    )
