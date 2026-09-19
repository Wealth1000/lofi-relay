# ORIENT — lofi-relay

A lightweight FastAPI service that turns YouTube livestreams into authenticated,
audio-only streams. Single end user (the owner). Deployed on Render's free tier.

## What it does

- `GET /lofi/{stream}` → raw AAC (ADTS) audio, chunked over HTTP, served by
  yt-dlp + FFmpeg. Requires `X-API-Key` header.
- `GET /lofi/{stream}/thumbnail` → JPEG cover art derived from the video ID
  (no yt-dlp extraction involved).
- `GET /streams` → list of configured streams + sources.
- `GET /health` → unauthenticated liveness probe.

## Architecture (one request path)

```
Client (X-API-Key)
  → FastAPI (auth, route)
    → yt-dlp (resolve YouTube manifest URL; Deno solves JS challenges)
      → FFmpeg (relay HLS audio as AAC to pipe)
        → client
```

## Key files

| File | Role |
| --- | --- |
| `app/main.py` | FastAPI app, routes, up-front resolve before streaming |
| `app/config.py` | All env config + the 7 hardcoded `STREAMS` map |
| `app/auth.py` | `X-API-Key` header check, `secrets.compare_digest` |
| `app/services/youtube.py` | yt-dlp extraction, cookie jar seeding/sync, manifest URL cache, global `_extract_lock` |
| `app/services/stream.py` | FFmpeg supervision: spawn, relay chunks, respawn on exit with backoff |
| `app/services/thumbnail.py` | i.ytimg.com thumbnail fetch + cache |
| `app/services/jar_store.py` | GitHub Gist as persistent cookie-jar store (pull/push) |
| `app/.lofi` | zsh client script: mpv + cava, cover-art fetch, keepalive pinger, restart loop |

## Operational facts

- Live at `https://lofi-relay.onrender.com` (Render free tier).
- Render spins down after 15 min of no *inbound* traffic; outbound audio does
  not keep it awake. Client runs a `/health` keepalive pinger (10 min) tied to
  playback, plus a restart loop (`.lofi`) that retries on disconnect.
- Cookies are the recurring failure point. They live in a private GitHub Gist
  (single source of truth), seeded into a runtime copy at
  `/tmp/youtube-cookies.txt` on cold boot, and kept fresh by a scheduled
  headless-Chromium job (`playwright-gist-updater`, runs on GitHub Actions —
  free, no card, always-on). The relay is read-only on the Gist. YouTube
  session rejection → 503 "re-export cookies required".
- yt-dlp extraction is slow and globally serialized (`_extract_lock`); the
  resolved manifest URL is cached `STREAM_URL_TTL` (3600s) and reused by
  respawns and second listeners.

## Open issues / improvement ideas (from last review)

1. No browser playback — raw ADTS has no container/CORS; only mpv works.
2. Render free tier kills playback every 15 min unless client keeps alive.
3. ~1 min cold-start before first byte: yt-dlp + Deno run before streaming
   begins; Deno cache is on ephemeral filesystem so it re-resolves on cold boot.
4. Cookies baked into image layers → rebuild/redeploy on every YouTube rotation.
5. Mid-stream `ReloginRequiredError` during a respawn breaks the generator
   after 200 headers are sent → client gets a truncated empty AAC body.
6. Manifest `expire` from YouTube is ignored; fixed `STREAM_URL_TTL` used.
7. No tests at all.
8. `format_id="91"` hardcoded with no fallback list.

## Stale leftovers (still on Render, not Fly)

- `fly.toml` is untracked; references a deployment that is not live.
- `.lofi` and `README.md` reference `https://lofi-relay.fly.dev` — should be
  the Render URL.

## Session restart instructions

- The user is the only end user; do not ask about broader audience.
- Re-read this file + `README.md` before exploring the codebase again.
- Run `curl -fsS -m 8 https://lofi-relay.onrender.com/health` to confirm the
  live service is up before assuming a bug is in the app.