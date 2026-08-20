import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.auth import verify_api_key
from app.config import LOG_LEVEL, STREAMS, UPSTREAM_FORMAT
from app.services.stream import audio_stream
from app.services.youtube import resolve_stream_url

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger(__name__)

app = FastAPI(
    title="Lofi Relay",
    description="Audio-only relay for livestreams",
    version="0.2.0",
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/streams")
async def streams(
    _: str = Depends(verify_api_key),
):
    return {
        "streams": [
            {"name": name, "source": source}
            for name, source in STREAMS.items()
        ]
    }


@app.get("/lofi/{stream_name}")
async def lofi(
    stream_name: str,
    _: str = Depends(verify_api_key),
):
    stream = STREAMS.get(stream_name)

    if stream is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown stream: {stream_name}",
        )

    # Resolve up front so an unusable stream fails as a 502. Once the streaming
    # response starts the headers are already sent, and a later failure could
    # only look like a truncated 200. This also warms the cache that
    # audio_stream() reads, so it starts relaying immediately.
    try:
        await resolve_stream_url(stream, UPSTREAM_FORMAT)
    except Exception as exc:
        log.exception("Failed to resolve %s", stream)

        raise HTTPException(
            status_code=502,
            detail="Unable to obtain livestream",
        ) from exc

    return StreamingResponse(
        audio_stream(stream, UPSTREAM_FORMAT),
        media_type="audio/aac",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
