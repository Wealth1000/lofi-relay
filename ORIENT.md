# ORIENT — lofi-relay

A lightweight FastAPI service that turns YouTube livestreams into authenticated,
audio-only streams. Single end user (the owner). Deployed on Render's free tier.

## What it does

Two paths, one client command (`.lofi`):

- **Resolver (default, `LOFI_BACKEND=json`):** a GitHub Actions cron (every
  30 min, free, rotating runner IPs, no card) resolves every source to a
  signed **audio-only** m3u8 and commits `data/streams.json`. The client
  fetches the document and plays the URL straight from YouTube's CDN.
  No host, no video bytes (~16 KB/s client line), no pinger.
- **Relay (fallback, `LOFI_BACKEND=relay`):** the FastAPI service on Render.
  - `GET /lofi/{stream}` → raw AAC, chunked over HTTP. Requires `X-API-Key`.
  - `GET /lofi/{stream}/thumbnail` → JPEG cover art (video-ID derived).
  - `GET /streams` → list of configured streams + sources.
  - `GET /health` → unauthenticated liveness probe.

## Architecture (one request path, relay backend)

```
Client (X-API-Key)
  → FastAPI (auth, route)
    → yt-dlp (resolve YouTube manifest URL; Deno solves JS challenges)
      → FFmpeg (relay HLS audio as AAC to pipe)
        → client
```

```
Resolver (json backend, primary)
GitHub Actions cron (30 min, fresh runner each run)
  → yt-dlp (audio-only format 234/233/bestaudio, Chrome impersonation,
     cookie jar pulled fresh from the Gist)
    → data/streams.json (committed; merge-preserved — a bad run never
      blanks a last-good URL; signed URLs live ~24h)
      → .lofi json backend → mpv → YouTube CDN
```

## Key files

| File | Role |
| --- | --- |
| `app/main.py` | FastAPI app, routes, up-front resolve before streaming (relay only) |
| `app/config.py` | Env config + canonical `STREAMS` map (22 keys) + `STREAM_LABELS` |
| `app/auth.py` | `X-API-Key` header check, `secrets.compare_digest` |
| `app/services/youtube.py` | yt-dlp extraction, cookie jar seeding/sync, manifest URL cache, global `_extract_lock` |
| `app/services/stream.py` | FFmpeg supervision: spawn, relay chunks, respawn on exit with backoff |
| `app/services/thumbnail.py` | i.ytimg.com thumbnail fetch + cache |
| `app/services/jar_store.py` | GitHub Gist as persistent cookie-jar store (pull/push) |
| `tools/resolve_streams.py` | Standalone resolver: pulls Gist jar, runs yt-dlp per stream, emits `data/streams.json` |
| `.github/workflows/resolve-streams.yml` | 30-min cron + manual dispatch; installs Deno + yt-dlp + curl_cffi, commits document |
| `data/streams.json` | Published audio-only URLs; public, committed by the cron |
| `tests/test_resolver.py` | Seam 1: stubbed yt-dlp, asserts on emitted document shape/status/exit-code |
| `.lofi` | zsh client: json backend (default) + relay fallback, cover art, cava, restart loop |
| `docs/migration-setup.html` | Owner setup guide (steps 1–6) for this migration |

## Operational facts

- **Primary path:** `data/streams.json` is a public file committed by the resolver cron every 30 minutes. `.lofi`'s json backend fetches it and plays the signed m3u8 directly from YouTube's CDN — no host, no video bytes.
- **Fallback path:** Render relay (`LOFI_BACKEND=relay`). Same behavior as before; Render spins down after 15 min of no inbound traffic; the relay's `/health` pinger is only needed here.
- **Cookie jar:** single source of truth is the private Gist. The relay reads it at cold boot; the resolver pulls a fresh copy each run. Both need `JAR_GIST_ID` / `JAR_GITHUB_TOKEN`.
- **YouTube format:** relay hardcodes `format_id="91"` (muxed HLS); resolver prefers `234/233/bestaudio/best` (audio-only rungs verified 2026-10-13).
- **Live stream validity:** observed ~24 h for signed URLs. Cron cadence (30 min) gives ample headroom.
- **Rate-limit posture:** the resolver treats a 429 as "defer the rest of the batch" (one 120 s cooldown sleep, then stops probing). A session rejection ("sign in"/"not a bot") publishes `status: stale` over the last good data and exits non-zero (red run).
- **Cross-IP caveat (open):** signed URLs embed the resolver IP in their signature. If YouTube starts refusing playback from different IPs, the second-IP proof test (Step 3 of `docs/migration-setup.html`) is the gate. Pre-planned escapes: Cloudflare Worker hop (if gate fails), mpv local-resolve fallback, or `LOFI_BACKEND=relay`.
- **Open issue:** the `halloween-lofi` channel is currently offline (seasonal); the resolver correctly records `url: null` + error. Same for `christmas-lofi` off-season.

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
8. `format_id="91"` hardcoded with no fallback list (relay-specific; frozen
   until teardown).

**Resolved by this migration (issue #3):**
- #5 / #6: the resolver's merge-preserved document removes the single-source-of-trust cache on Render; stale entries fall back to last-good URLs without corrupting.
- #7: test seam 1 is landed (`tests/test_resolver.py`).
- Cross-IP YouTube signature gate: documented in `docs/migration-setup.html` Step 3. Not yet verified from a residential IP — Render stays until that passes.

## Stale leftovers (still on Render, not Fly)

- `fly.toml` is untracked; references a deployment that is not live.
- `.lofi` and `README.md` previously referenced `https://lofi-relay.fly.dev`
  — now point at `https://lofi-relay.onrender.com`.
- `app/config.py` still hardcodes `UPSTREAM_FORMAT="91"` — relay is frozen
  until teardown (see Step 6 of `docs/migration-setup.html`).

## Session restart instructions

- The user is the only end user; do not ask about broader audience.
- Re-read this file + `README.md` before exploring the codebase again.
- Run `curl -fsS -m 8 https://lofi-relay.onrender.com/health` to confirm the
  live service is up before assuming a bug is in the app.