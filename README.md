# Lofi Relay

A lightweight FastAPI service that turns YouTube livestreams into authenticated, audio-only streams.

Lofi Relay uses **yt-dlp** to resolve YouTube livestreams, **Deno** to handle YouTube's JavaScript challenges, and **FFmpeg** to relay the resulting HLS audio stream as AAC.

Since YouTube began flagging the relay's static Render egress IP, this repo now has **two ways to reach a stream**:

* **The resolver (primary path):** a free GitHub Actions cron resolves every stream to a signed **audio-only** m3u8 URL and publishes them to [`data/streams.json`](data/streams.json). Clients (`.lofi`) play the URL straight from YouTube's CDN — no host, no card, no video bytes anywhere (the audio-only variant never carries a video track), and no keep-alive pinger.
* **The relay (fallback below):** the FastAPI service as before. Kept alive behind `LOFI_BACKEND=relay` in case YouTube "forgets" about the resolver's URLs again.

## Features

* 🎵 Audio-only YouTube livestream relay
* 📡 Resolver cron that publishes signed audio-only m3u8 URLs (no host, no video bytes)
* 🖼️ Serves each stream's YouTube artwork for client media widgets
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

### Stream Artwork

```http
GET /lofi/{stream}/thumbnail
```

Requires authentication. Returns the stream's YouTube thumbnail as `image/jpeg`.

Example:

```bash
curl -H "X-API-Key: $LOFI_API_KEY" \
  -o cover.jpg \
  https://lofi-relay.onrender.com/lofi/relax-study/thumbnail
```

The image URL is derived from the video ID, so this endpoint **never runs
yt-dlp** — artwork never queues behind stream extraction. `maxresdefault.jpg` is
preferred, falling back to `hqdefault.jpg`, which always exists. Responses are
cached in memory for `THUMBNAIL_TTL` seconds.

---

### Lofi Streams

All stream endpoints require authentication. The canonical names are the keys of
`STREAMS` in [`app/config.py`](app/config.py); the resolver publishes one entry
per name in `data/streams.json` and `.lofi` keys off the same names.

| Endpoint                    | Stream              | Source (video ID) |
| --------------------------- | ------------------- | ----------------- |
| `/lofi/lofi-house`          | Lofi House          | `3PFJ9SETS4M`     |
| `/lofi/relax-study`         | Relax / Study       | `rFZHOHl-L8A`     |
| `/lofi/summer-lofi`         | Summer Lofi         | `0muHFBSiybw`     |
| `/lofi/sleep-dream`         | Sleep / Dream       | `VAlMDl00mYY`     |
| `/lofi/synth-ambient`       | Synth Ambient       | `GSfT7H87zq4`     |
| `/lofi/study-with-me`       | Study With Me       | `qGohtGC5Rtk`     |
| `/lofi/sad-rainy`           | Rain / Sad          | `CwPCy1GLS38`     |
| `/lofi/asian-relax-study`   | Asian Relax / Study | `1Tl2FtV06qo`     |
| `/lofi/jazz-chill-study`    | Jazz / Chill Study  | `E2vONfzoyRI`     |
| `/lofi/sleep-chill`         | Sleep Chill         | `JD-kMIpDfnY`     |
| `/lofi/piano-focus`         | Piano / Focus       | `N0snMcR6aaA`     |
| `/lofi/christmas-lofi`      | Christmas Lofi      | `XSXEaikz0Bc`     |
| `/lofi/classical-study`     | Classical Study     | `jXAEIWcGXwE`     |
| `/lofi/relaxing-jazz`       | Relaxing Jazz       | `A8jDx9TLMQc`     |
| `/lofi/halloween-lofi`      | Halloween Lofi      | `3GQY80jyysQ`     |
| `/lofi/fireplace-ambience`  | Fireplace Ambience  | `q_4KI-ChIIs`     |
| `/lofi/chill-guitar`        | Chill Guitar        | `E_XmwjgRLz8`     |
| `/lofi/sleep-ambient`       | Sleep Ambient       | `xORCbIptqcc`     |
| `/lofi/medieval-lofi`       | Medieval Lofi       | `IxPANmjPaek`     |
| `/lofi/gentle-rain`         | Gentle Rain         | `-OekvEFm1lo`     |
| `/lofi/dark-ambient`        | Dark Ambient        | `S_MOd40zlYU`     |
| `/lofi/synthwave`          | Chill Game          | `4xDzrJKXOOY`     |

Seasonal channels (`christmas-lofi`, `halloween-lofi`) are offline most of the
year; the resolver records them as `url: null` + an error, which is correct
behaviour, not a resolver bug.

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

## Artwork in the Media Widget

Desktop media widgets (swaync, waybar, GNOME, KDE) read exactly one field:
`mpris:artUrl` in the player's MPRIS metadata. mpv publishes that from
`--cover-art-files`, which takes a **local path** — so the artwork has to be
fetched to a file first:

```bash
cover="$(mktemp --suffix=.jpg)"
trap 'rm -f "$cover"' EXIT INT TERM

curl -fsS -H "X-API-Key: $LOFI_API_KEY" \
  -o "$cover" \
  https://lofi-relay.onrender.com/lofi/relax-study/thumbnail

mpv \
  --no-video \
  --http-header-fields="X-API-Key: $LOFI_API_KEY" \
  --cover-art-files="$cover" \
  --force-media-title="Relax / Study" \
  https://lofi-relay.onrender.com/lofi/relax-study
```

`--force-media-title` matters too: without it the notification shows the bare
relay URL instead of a name.

This works with `--no-video` and with the raw ADTS stream the API serves — mpv
registers the image as an unselected track (`○ Image --vid=1 ... [external]`) and
mpv-mpris still exports its path. Requires mpv 0.36 or newer for
`--cover-art-files`.

Embedding the image *in the audio* instead — an MP4 `attached_pic` stream — does
**not** achieve this, for two independent reasons:

* `mpris:artUrl` is a URI, so it can only point at a file. There is no URI for
  bytes buried inside a container, which is why mpv-mpris reads
  `--cover-art-files` rather than the playing stream.
* The relay writes to a pipe, and MP4 has nowhere to put its `moov` atom on a
  non-seekable output.

Embedded cover art only feeds mpv's own video window, which audio-only clients
have switched off.

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

This diagram describes the **relay fallback** (used when `LOFI_BACKEND=relay`). The primary client path is the resolver, below.

---

## Resolver (Primary Client Path)

A scheduled GitHub Actions workflow (`.github/workflows/resolve-streams.yml`)
runs every 30 minutes on a **fresh, ephemeral runner** — the runner's egress
IP rotates per run, unlike Render's static IP that YouTube has started
flagging. Public-repo Actions are free and uncapped, and no card is required
anywhere in this path.

Each run:

1. Pulls the cookie jar from the private Gist (same single source of truth as
   the relay; the jar is armor against flagged runner IPs, not a gate —
   public channels resolve jar-less).
2. Resolves every source in `app/config.py::STREAMS` with yt-dlp, requesting
   the **audio-only** HLS rungs first (`234/233/bestaudio/best`). Verified
   2026-10-13: itag-234 live playlists serve 5-second segments that are pure
   AAC (~130 kb/s, no video track). URL-only extraction — the runner never
   downloads media bytes.
3. Publishes the result as [`data/streams.json`](data/streams.json), committed
   by the workflow. The document is **merge-preserved**: a failed refresh
   keeps the last known-good URL (signed m3u8s stay valid for hours; observed
   ~24 h), a session-level rejection publishes `status: "stale"` over the
   last good data, and a run with nothing playable at all fails red.

The client side is the `json` backend of `.lofi` (the default): it fetches the
document once, plays the signed m3u8 with mpv, fetches cover art straight from
`i.ytimg.com` by video ID, and re-fetches the document on every restart-loop
iteration so a rotated URL self-heals within one cron tick.

```text
GitHub Actions cron (30 min, rotating IP, free, no card)
  │  yt-dlp + cookie-jar Gist → signed audio-only m3u8 × 22 streams
  ▼
data/streams.json (public raw URL)
  │
  ▼
your .lofi (json backend) → mpv → YouTube CDN (audio only, ~16 KB/s)
```

**Data cost of the whole design:** the client line carries audio only
(~130 kb/s ≈ 16 KB/s). No host downloads video, because the audio-only
variant has no video track — the "who bears the video egress" question no
longer has a video to bear.

**Known unknown (the second-IP gate):** the signed URLs embed the resolving
IP in their signature parameters. If YouTube ever IP-binds them, a
runner-resolved URL would 403 from a residential IP. `lofi -health` probes a
fresh entry, and the pre-planned escapes are: (a) the Cloudflare Worker hop
(CF's network fetches the segments), (b) mpv's local yt-dlp resolve (your
residential IP is exactly the one YouTube likes), (c) `LOFI_BACKEND=relay`.
Until the gate passes from your machine, Render stays up.

---

## Project Structure

```text
lofi-relay/
├── app/
│   ├── __init__.py
│   ├── auth.py
│   ├── config.py          # STREAMS (canonical names), STREAM_LABELS, env config
│   ├── main.py
│   └── services/
│       ├── __init__.py
│       ├── stream.py
│       ├── thumbnail.py
│       ├── youtube.py
│       └── jar_store.py   # cookie-jar Gist (shared by relay + resolver)
│
├── tools/
│   ├── __init__.py
│   └── resolve_streams.py # standalone resolver (no FastAPI)
├── tests/
│   └── test_resolver.py
├── .github/
│   └── workflows/
│       └── resolve-streams.yml  # the 30-min cron (primary path)
├── data/
│   └── streams.json       # published audio-only URLs (committed by the cron)
├── .lofi                 # zsh client: json backend (default) + relay fallback
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
├── .dockerignore
├── .gitignore
└── README.md
```

---

## Configuration

Environment variables:

```env
LOFI_API_KEY=your-secret-api-key
```

YouTube cookies are **not** configured via env and are **not** in the image —
they live in a private GitHub Gist (see below). The image is cookie-free, so
YouTube rotating session cookies never requires a rebuild or redeploy.

Optional tuning:

| Variable                   | Default | Purpose                                              |
| -------------------------- | ------- | ---------------------------------------------------- |
| `LOG_LEVEL`                | `INFO`  | Logging verbosity                                    |
| `STREAM_URL_TTL`           | `3600`  | Seconds a resolved manifest URL may be reused        |
| `THUMBNAIL_TTL`            | `21600` | Seconds a fetched thumbnail may be reused            |
| `THUMBNAIL_TIMEOUT`        | `15`    | Thumbnail fetch timeout in seconds                   |
| `THUMBNAIL_MAX_BYTES`      | `4MiB`  | Cap on thumbnail data buffered in memory             |
| `FFMPEG_MAX_RESTARTS`      | `5`     | Consecutive relay failures before giving up          |
| `FFMPEG_RESTART_DELAY`     | `2`     | Base restart backoff in seconds (grows per attempt)  |
| `FFMPEG_RESTART_DELAY_MAX` | `30`    | Backoff ceiling in seconds                           |
| `JAR_GIST_ID`              | unset   | Gist ID for the cookie-jar store (see below)         |
| `JAR_GITHUB_TOKEN`         | unset   | Read-only gist-scoped PAT for the store              |
| `JAR_TIMEOUT`              | `15`    | Gist API timeout in seconds                          |

### Cookie-jar store

The cookie jar lives in a private GitHub Gist (file `yt-cookies.txt`) and is
the **single source of truth**. The relay is read-only on it:

- **Cold boot:** the relay pulls the jar from the Gist and seeds the runtime
  copy. If the Gist is unconfigured, unreachable, or holds no usable jar, the
  relay fails loud at startup (`RuntimeError`) instead of silently serving on
  a dead jar.
- **Runtime:** yt-dlp refreshes the runtime copy during extraction. The relay
  does **not** push back — a scheduled headless-Chromium job
  (`playwright-gist-updater`) is the sole writer, so there is no race.
- **Freshness:** the browser job logs into a throwaway Google account in
  Chromium and re-exports the jar to the Gist on a fixed schedule (every 6h),
  so the jar is always re-armed before it could go stale — even with no
  streaming traffic.

When YouTube rejects the jar outright, `/lofi/*` returns a `503` with
`YouTube session rejected` in the detail, and the relay stops instead of
retrying — the fix is a fresh cookie export, not a retry. The resolver's cron
fails the same way: it publishes `status: "stale"` over the last good
document (preserving the playable URLs) and flags the run red.

The `JAR_GIST_ID` / `JAR_GITHUB_TOKEN` pair is also exposed to the resolver
workflow as Actions repository secrets, so the cron and the relay arm from
the same jar.

The browser job never overwrites the Gist on a failed login: it keeps the
last known-good jar and exits non-zero so the failure is visible. A manual
re-export to the Gist is the escape hatch if the headless login is
challenged.

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

## Free Tier Behaviour (relay backend)

The relay deployment runs on Render's free tier, which
[spins a service down](https://render.com/docs/free) after **15 minutes without
inbound traffic**:

> Render spins down a Free web service that goes 15 minutes without receiving
> any inbound traffic. This includes both HTTP requests and WebSocket messages
> from existing connections.

An in-flight audio stream is *outbound* traffic, so **it does not keep the
service awake**. Left alone, playback dies roughly 15 minutes into a listening
session. Render also notes it "might restart a Free web service at any time".

This only affects the **relay backend** (`LOFI_BACKEND=relay`): the pinger
below is what the relay path of `.lofi` runs while playing, tied to playback
so it never burns instance-hours around the clock. The `json` backend has no
host to keep awake — Cloudflare and GitHub Actions never sleep.

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

### Resolver

The resolver is standalone: it imports only `app.config` (the stream map) and
the cookie-jar store, never the FastAPI app. Run it locally to preview a
document without touching the published one:

```bash
python -m tools.resolve_streams --dry-run
```

Or write a real document (the workflow does exactly this, then commits):

```bash
python -m tools.resolve_streams
```

Set `JAR_GIST_ID` / `JAR_GITHUB_TOKEN` in the environment to resolve with the
Gist's cookie jar; unset, it resolves jar-less, which public channels accept.

Run the tests (seam: a stubbed `yt_dlp` stands in for the YouTube boundary,
assertions are on the published document):

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
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
  -p 8000:8000 \
  lofi-relay
```

The image is cookie-free: at startup it pulls the cookie jar from the Gist
(`JAR_GIST_ID` / `JAR_GITHUB_TOKEN` in `.env`). If the Gist is unconfigured or
returns no usable jar, the container exits with a clear error instead of
serving on a dead jar.

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
* Put the API key directly into source code
* Publish the API key in client-side source code intended for public distribution

The production service expects the API key through the `X-API-Key` HTTP header.

---

## Cookie refresh automation

Freshness for the cookie jar comes from a separate, always-on job:
[`playwright-gist-updater`](../playwright-gist-updater) — a scheduled
GitHub Actions workflow (free, no credit card required) that logs into a
throwaway Google account in headless Chromium and re-exports the jar to the
Gist every 6 hours. See that repo's README for setup, including the
never-overwrite-on-failure guardrail.

---

## Current Stream List

The canonical list is `STREAMS` in [`app/config.py`](app/config.py) (22 streams,
names in the table above under "Lofi Streams") and `data/streams.json`, which
the resolver publishes from it. Add a stream by adding one key to `STREAMS`
(+ a label in `STREAM_LABELS`) and a matching `flag:name:label` line in `.lofi`.

---

## Production

The relay (fallback) is hosted on Render:

```text
https://lofi-relay.onrender.com
```

The service exposes the FastAPI application on the port supplied by the hosting environment.

The primary client path needs no hosted service: `data/streams.json` is
committed to this public repo by the resolver cron, and clients fetch it
straight from GitHub.

---

## Tech Stack

* Python
* FastAPI
* Uvicorn
* yt-dlp
* Deno
* FFmpeg
* Docker
* Render (relay fallback)
* GitHub Actions (resolver cron + cookie refresh)

---

## Status

**Resolver: live.** The cron publishes `data/streams.json` every 30 minutes;
verify it from a residential IP with `lofi -health` before treating the
relay as disposable.

**Relay: operational fallback**, tested end-to-end:

```text
API authentication
→ yt-dlp
→ YouTube JS challenge solving
→ HLS extraction
→ FFmpeg
→ AAC streaming
→ HTTP client
```

The client can consume the `/lofi/*` relay endpoints directly, or the
resolver's published URLs (default).
