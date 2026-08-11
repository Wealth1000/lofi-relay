from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.auth import verify_api_key
from app.config import STREAMS, UPSTREAM_FORMAT
from app.services.stream import audio_stream
from app.services.youtube import get_stream_url


app = FastAPI(
    title="Lofi Relay",
    description="Audio-only relay for livestreams",
    version="0.1.0",
)


@app.get("/health")
async def health():
    return {"status": "ok"}


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

    try:
        url = get_stream_url(
            stream,
            UPSTREAM_FORMAT,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to obtain livestream",
        ) from exc

    return StreamingResponse(
        audio_stream(url),
        media_type="audio/aac",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

