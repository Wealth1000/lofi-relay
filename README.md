# Lofi Relay

A lightweight FastAPI service that turns YouTube livestreams into authenticated, audio-only streams.

Lofi Relay uses **yt-dlp** to resolve YouTube livestreams, **Deno** to handle YouTube's JavaScript challenges, and **FFmpeg** to relay the resulting HLS audio stream as AAC.

## Features

* 🎵 Audio-only YouTube livestream relay
* 🔐 API-key authentication
* ⚡ FastAPI-based HTTP API
* 🎬 yt-dlp for YouTube stream extraction
* 🦕 Deno JavaScript runtime for yt-dlp
* 🎧 FFmpeg audio processing
* 🐳 Docker support
* 🔄 Supports long-running livestreams
* ♻️ Automatic recovery from upstream failures
* 🚫 Does not download or permanently store livestream media

---

## API

### Base URL

Production:

`https://lofi-relay.onrender.com`

### Authentication

All `/lofi/*` endpoints require the API key to be supplied through the `X-API-Key` header.

```http
X-API-Key: YOUR_API_KEY
```

The API key is configured through the `LOFI_API_KEY` environment variable.

---

## Available Endpoints

### Health Check

```http
GET /health
```

Does not require authentication.

Example:

```bash
curl https://lofi-relay.onrender.com/health
```

Response:

```json
{
  "status": "ok"
}
```

---

### Stream List

```http
GET /streams
```

Requires authentication. Lists the available stream names and their sources.

Example:

```bash
curl -H "X-API-Key: $LOFI_API_KEY" https://lofi-relay.onrender.com/streams
```

Response:

```json
{
  "streams": [
    {
      "name": "relax-study",
      "source": "https://www.youtube.com/watch?v=rFZHOHl-L8A"
    }
  ]
}
```

---

### Lofi Streams

All stream endpoints require authentication.

#### Relax / Study

```http
GET /lofi/relax-study
```

Source:

`https://www.youtube.com/watch?v=rFZHOHl-L8A`

---

#### Chill Game

```http
GET /lofi/chill-game
```

Source:

`https://www.youtube.com/live/4xDzrJKXOOY`

---

#### Asian Relax / Study

```http
GET /lofi/asian-relax-study
```

Source:

`https://www.youtube.com/live/1Tl2FtV06qo`

---

#### Jazz Chill / Study

```http
GET /lofi/jazz-chill-study
```

Source:

`https://www.youtube.com/live/E2vONfzoyRI`

---

#### Sleep / Dream

```http
GET /lofi/sleep-dream
```

Source:

`https://www.youtube.com/live/VAlMDl00mYY`

---

#### Rain / Sad

```http
GET /lofi/rain-sad
```

Source:

`https://www.youtube.com/live/CwPCy1GLS38`

---

#### Piano / Focus

```http
GET /lofi/piano-focus
```

Source:

`https://www.youtube.com/live/N0snMcR6aaA`

---

## Example Request

```bash
curl \
  -H "X-API-Key: $LOFI_API_KEY" \
  https://lofi-relay.onrender.com/lofi/relax-study
```

The endpoint returns an AAC audio stream:

```http
HTTP/2 200
content-type: audio/aac
```

The stream is intended to be consumed directly by an audio player rather than displayed in a terminal.

For example, with `mpv`:

```bash
mpv \
  --no-video \
  --http-header-fields="X-API-Key: $LOFI_API_KEY" \
  https://lofi-relay.onrender.com/lofi/relax-study
```

---

## Architecture

```text
Client
  │
  │ X-API-Key
  ▼
FastAPI
  │
  ├── API authentication
  │
  └── Stream selection
          │
          ▼
       yt-dlp
          │
          ├── YouTube webpage
          ├── Player API
          ├── Deno JS challenge solving
          └── HLS/m3u8 extraction
                  │
                  ▼
              FFmpeg
                  │
                  ▼
             AAC stream
                  │
                  ▼
                Client
```

The service does not download the entire livestream. yt-dlp resolves the current YouTube stream URL, after which FFmpeg relays the audio to the client.

---

## Project Structure

```text
lofi-relay/
├── app/
│   ├── __init__.py
│   ├── auth.py
│   ├── config.py
│   ├── main.py
│   └── services/
│       ├── __init__.py
│       ├── stream.py
│       └── youtube.py
│
├── Dockerfile
├── requirements.txt
├── .dockerignore
├── .gitignore
└── README.md
```

---

## Configuration

Environment variables:

```env
LOFI_API_KEY=your-secret-api-key
YOUTUBE_COOKIES_PATH=/etc/secrets/youtube-cookies.txt
```

`YOUTUBE_COOKIES_PATH` defaults to:

```text
/etc/secrets/youtube-cookies.txt
```

The YouTube cookies file should **never be committed to the repository**.

Optional tuning:

| Variable                   | Default | Purpose                                              |
| -------------------------- | ------- | ---------------------------------------------------- |
| `LOG_LEVEL`                | `INFO`  | Logging verbosity                                    |
| `STREAM_URL_TTL`           | `3600`  | Seconds a resolved manifest URL may be reused        |
| `FFMPEG_MAX_RESTARTS`      | `5`     | Consecutive relay failures before giving up          |
| `FFMPEG_RESTART_DELAY`     | `2`     | Base restart backoff in seconds (grows per attempt)  |
| `FFMPEG_RESTART_DELAY_MAX` | `30`    | Backoff ceiling in seconds                           |

---

## Stream Reliability

A livestream is never supposed to end, so the relay treats FFmpeg exiting as a
fault rather than end-of-stream.

When FFmpeg dies — an expired manifest, a segment 404, YouTube restarting the
livestream — the relay re-resolves the stream URL and respawns FFmpeg **inside
the same HTTP response**, with backoff. The client sees one continuous stream
instead of a disconnect. After `FFMPEG_MAX_RESTARTS` consecutive failures the
relay gives up and closes the response.

Two supporting details:

* **Manifest URLs are cached** for `STREAM_URL_TTL` seconds. yt-dlp extraction
  is slow (webpage fetch, player API, Deno JS challenge), so a respawn or a
  second listener reuses the resolved URL instead of paying for it again.
  Extraction is serialized, since every call rewrites the same runtime cookie
  file.
* **Extraction runs in a worker thread.** It used to block the event loop, which
  starved every other in-flight relay — those fell behind the HLS live edge and
  died on expired segments, so starting one stream could kill another.

---

## Free Tier Behaviour

The production deployment runs on Render's free tier, which
[spins a service down](https://render.com/docs/free) after **15 minutes without
inbound traffic**:

> Render spins down a Free web service that goes 15 minutes without receiving
> any inbound traffic. This includes both HTTP requests and WebSocket messages
> from existing connections.

An in-flight audio stream is *outbound* traffic, so **it does not keep the
service awake**. Left alone, playback dies roughly 15 minutes into a listening
session. Render also notes it "might restart a Free web service at any time".

Clients are therefore expected to send a periodic keep-alive request while
playing:

```bash
while sleep 600; do
  curl -fsS -o /dev/null https://lofi-relay.onrender.com/health || true
done
```

Keep the pinger tied to playback, and do **not** run it around the clock: the
free tier grants 750 instance hours per month (~31 days), so a continuous
pinger would exhaust the quota and get the service suspended.

Spinning back up takes about a minute, so clients should also retry rather than
treating a dropped stream as fatal.

---

## Local Development

Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the API:

```bash
uvicorn app.main:app --reload
```

The API will be available at:

```text
http://127.0.0.1:8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

---

## Docker

Build the image:

```bash
docker build -t lofi-relay .
```

Run it locally:

```bash
docker run --rm -it \
  --env-file .env \
  -v "$(pwd)/youtube-cookies.txt:/etc/secrets/youtube-cookies.txt:ro,Z" \
  -p 8000:8000 \
  lofi-relay
```

The API will then be available at:

```text
http://localhost:8000
```

---

## Testing YouTube Extraction

To test yt-dlp independently of FastAPI:

```bash
docker run --rm -it \
  --env-file .env \
  -v "$(pwd)/youtube-cookies.txt:/etc/secrets/youtube-cookies.txt:ro,Z" \
  lofi-relay \
  python -c "from app.services.youtube import get_stream_url; print(get_stream_url('https://www.youtube.com/watch?v=rFZHOHl-L8A', '91'))"
```

A successful extraction should return a `manifest.googlevideo.com` HLS playlist URL.

---

## Stream Format

The API currently returns:

```text
audio/aac
```

FFmpeg receives the YouTube HLS stream and relays the AAC audio without transcoding where possible.

The stream is delivered using HTTP chunked streaming through FastAPI's `StreamingResponse`.

---

## Security

The API key should be treated as a secret.

Do not:

* Commit `.env` files
* Commit `youtube-cookies.txt`
* Put the API key directly into source code
* Publish the API key in client-side source code intended for public distribution

The production service expects the API key through the `X-API-Key` HTTP header.

---

## Current Stream List

| Endpoint                  | Stream              |
| ------------------------- | ------------------- |
| `/lofi/relax-study`       | Relax / Study       |
| `/lofi/chill-game`        | Chill Game          |
| `/lofi/asian-relax-study` | Asian Relax / Study |
| `/lofi/jazz-chill-study`  | Jazz Chill / Study  |
| `/lofi/sleep-dream`       | Sleep / Dream       |
| `/lofi/rain-sad`          | Rain / Sad          |
| `/lofi/piano-focus`       | Piano / Focus       |

Additional streams can be added in `app/config.py`.

---

## Production

The current production deployment is hosted on Render:

```text
https://lofi-relay.onrender.com
```

The service exposes the FastAPI application on the port supplied by the hosting environment.

---

## Tech Stack

* Python
* FastAPI
* Uvicorn
* yt-dlp
* Deno
* FFmpeg
* Docker
* Render

---

## Status

**Complete and operational.**

The production pipeline has been tested successfully from client request through:

```text
API authentication
→ yt-dlp
→ YouTube JS challenge solving
→ HLS extraction
→ FFmpeg
→ AAC streaming
→ HTTP client
```

The client application can consume the individual `/lofi/*` endpoints directly.
