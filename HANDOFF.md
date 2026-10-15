# Migration Handoff — lofi-relay Off Render → Audio-Only via GitHub Actions + CF Worker

**Date**: 2026-10-14  
**Owner**: Wealth1000  
**Repo**: `Wealth1000/lofi-relay` (public, branch `master`)

---

## Status at Last Session

The migration off Render is **substantially complete**. The resolver cron and client rewrite are live. YouTube's audio-only HLS rungs (itag 234/233) are **not** accessible from a datacenter egress IP, so the published `data/streams.json` contains **muxed-fallback** URLs (itag 96, ~520 MB/hour) — this is by design now: the Cloudflare Worker strips the video at the edge, and the client downloads only audio (~58 MB/hour equivalent, measured 15 KB/s).

The Cloudflare Worker is **deployed and verified** (2026-10-15): JSON contract, PMT-based segment stripping, 30 s mpv playback, 10× bandwidth reduction all confirmed. Remaining: 7-day worker soak (informational), then rotate the API key into env vars and scrub it from history (Priority 6). Render stays up as a dormant fallback — no teardown.

---

## What Has Been Done

### 1. Resolver Script — `tools/resolve_streams.py` ✅
- Standalone Python script, no FastAPI dependency.
- Imports only `app.config` (stream map + env) and `app.services.jar_store` (Gist cookie jar).
- Runs in a batch across all 22 streams in `app.config.STREAMS`.
- Merge-preserved document output: a failed run never blanks a previously-good URL.
- Format string: `"234/233/bestaudio/best"` — tries audio-only rungs first, falls back to muxed.
- Client strategy rotation: `(None, ("mweb", "web_embedded"))` — default client first, mweb pair as fallback.
- Session classification: login rejection → stop run and preserve last doc; rate limit → sleep 120s then defer.
- Writes to `data/streams.json` with fields: `status`, `updated_at`, `last_error`, `streams: {name: {url, variant, format_id, stale, error}}`.

### 2. GitHub Actions Workflow — `.github/workflows/resolve-streams.yml` ✅
- Cron: `*/30 * * * *` (every 30 minutes).
- Manual dispatch also supported.
- Steps: checkout → setup Python 3.12 → install Deno (for yt-dlp ejs) → install `yt-dlp[default] curl_cffi>=0.10,<0.17` → run resolver → commit+push `data/streams.json`.
- Requires secrets: `JAR_GIST_ID`, `JAR_GITHUB_TOKEN`.
- Concurrency: `resolve-streams` group, no cancel-in-progress (prevents overlap).
- Timeout: 25 minutes per run.
- **Last successful run**: `38482859361` (2026-10-13T23:14Z) — 21 fresh, 1 stale (halloween-lofi offline). All 21 resolved as `muxed-fallback` (itag 96).

### 3. Test Suite — `tests/test_resolver.py` ✅
- 9 tests, all passing locally.
- Seam 1: stubbed `yt_dlp.YoutubeDL.extract_info` to assert on emitted document shape, status, exit code.
- Requires `pytest` (installed in `.venv`).

### 4. Client — `.lofi` ✅ (rewritten, 3 backends)
Three backends, selected via `LOFI_BACKEND` env var (default: `worker`):

| Backend | Behavior | When to use |
|---------|----------|-------------|
| `worker` (default) | Fetches document from CF Worker `/api/play/<endpoint>`, plays result directly via mpv | **Primary path — deployed and verified 2026-10-15** |
| `json` | Fetches `data/streams.json` directly from GitHub raw, plays the raw URL | Debugging, or when your residential IP gets audio-only |
| `relay` | Falls back to Render relay at `https://lofi-relay.onrender.com/lofi/<endpoint>` | Dormant fallback, kept indefinitely (no teardown); keepalive pinger stays on |

Key details:
- `LOFI_WORKER_BASE` env var points to the deployed Worker URL (e.g. `https://lofi-relay.<your-name>.workers.dev`). Set it in the local, gitignored `.env` (or `~/.zshrc`) — the Worker URL must never be committed.
- Cover art: fetched from `i.ytimg.com` using `video_id` from the document. No API key needed.
- mpv args: `--no-video --really-quiet --cache=yes --cache-secs=60 --demuxer-max-bytes=64MiB --stream-lavf-o=reconnect=1,reconnect_streamed=1,reconnect_delay_max=5`.
- Restart loop: 3-second gap between attempts, gives up after 10 consecutive instant failures.
- Health check (`lofi -health`): for worker backend, pings `/api/streams` and reports status/playable count. For json, probes the freshest URL with ffprobe to confirm audio-only. For relay, pings `/health`.

### 5. Config — `app/config.py` ✅
- Added `STREAM_LABELS` dict (single source of truth for stream names + display labels).
- 22 canonical stream keys matching the client's stream table.
- Existing constants preserved: `UPSTREAM_FORMAT="91"`, `STREAM_URL_TTL`, `THUMBNAIL_TTL`, etc.

### 6. Seed Document — `data/streams.json` ✅
- Committed to repo, publicly readable at:
  ```
  https://raw.githubusercontent.com/Wealth1000/lofi-relay/master/data/streams.json
  ```
- Current state: 21 fresh entries (all `variant: muxed-fallback`, `format_id: 96`), 1 stale (`halloween-lofi`, offline seasonally).
- **All URLs contain datacenter IP** (`172.212.167.164`) embedded in the signature.
- Signed URL expiry: ~24 hours (observed `expire/1791954806` ~24h from publish).

### 7. Documentation ✅
- `README.md`: Updated with two-path architecture, 22-entry stream table, resolver section, free-tier notes scoped to relay.
- `ORIENT.md`: Updated key files table, operational facts, open issues.
- `docs/migration-setup.html`: Owner setup guide (Steps 1–6 for manual migration).

### 8. Cloudflare Worker — `worker/` ✅ (deployed, verified 2026-10-15)
Three files in `worker/`:
- `index.ts` — Hono-based Worker with 4 routes:
  - `GET /api/streams` — proxies the document from GitHub raw URL, cached 5 min (Cache API) + 60 s in-memory cache used by every other route
  - `GET /api/play/<endpoint>` — **always** returns `{"url": "...", "variant": "..."}`; for audio-only streams passes through the signed URL directly; for muxed-fallback returns the Worker's own `/api/m3u8/<endpoint>` playlist URL (`.lofi` does `jq -r '.url'` on this response — raw m3u8 text here broke playback before this contract was fixed)
  - `GET /api/m3u8/<endpoint>` — fetches YouTube's manifest, rewrites every segment URI to `/api/seg/<endpoint>?n=<media-sequence>` using **absolute** media-sequence numbers (`#EXT-X-MEDIA-SEQUENCE`), not window positions, so indices don't drift under the rolling live window; out-of-window segment requests 404 and the player re-reads the playlist
  - `GET /api/seg/<endpoint>?n=<seq>` — downloads the muxed segment from YouTube and returns audio-only:
    1. PAT → PMT parsing discovers the real audio PID (not hardcoded; 0x101 is only the fallback) and the video PID
    2. Primary: extracts the audio PES payloads, strips PES headers (correct adaptation-field + payload-unit-start handling) — output is YouTube's native ADTS AAC (~81 KB / 5 s segment, `audio/aac`)
    3. Fallback: if PES structure is unexpected, drops video/null PID packets and returns the rest as `video/MP2T`
    4. Otherwise 502 — never returns undecodable bytes to the client
- `package.json` — dependencies: `hono`, dev deps: `wrangler`, `@cloudflare/workers-types` (installed with pnpm; `pnpm-workspace.yaml` pins `allowBuilds` for `esbuild`/`sharp`/`workerd`)
- `wrangler.toml` — name `lofi-relay`, main `index.ts`, compatibility `2024-01-01`, `nodejs_compat` flag

**Verified end-to-end (2026-10-15)**: `/api/play` → JSON ✓; segment = 81 KB, `aac (LC) 44100 Hz stereo 130 kb/s`, 5.00 s, no video stream ✓; mpv played the worker playlist for 30 s, exit 0 ✓; measured client transfer = 477 KB / 30 s = **15 KB/s (~130 kb/s)** vs ~150 KB/s for muxed video ✓.

---

## What Still Needs to Be Done

### Priorities 1–5 ✅ — DONE (2026-10-15)
Deploy, `LOFI_WORKER_BASE` (local, gitignored), Worker verification, transfer-cost validation, and the second-IP proof all passed on the residential machine:
- `/api/streams` → `status ok`, `playable 21/22`
- `/api/play/relax-study` → JSON, url points at `/api/m3u8/relax-study`
- `/api/seg/...?n=<seq>` → 81 KB, `aac (LC) 44100 Hz stereo`, ffprobe shows **audio only**
- mpv 30 s play test → exit 0, **477 KB / 30 s = 15 KB/s (~130 kb/s)**, vs ~150 KB/s for muxed video (10× reduction)

### Priority 6 — Render Kept as Dormant Fallback + API Key Cleanup
**Decision (2026-10-15): Render is NOT torn down.** It stays indefinitely as an independent, Cloudflare-free fallback (`LOFI_BACKEND=relay`), kept warm by the keepalive pinger. Rationale: YouTube's audio-only rung denial for datacenter IPs is IP-class reputation, not a temporary flag that expires — but relay still insures against *worker-side* failure (CF outage, CF account trouble), which is a different risk from the flag.

1. Soak: run the worker as primary for 7 clean days and confirm `lofi -health` stays green. Informational — nothing is gated on it anymore.
2. Rotate the API key (mandatory regardless of relay — the old key exists in git history):
   a. Set a new `LOFI_API_KEY` on Render. The app reads it from the environment (`app/auth.py`), so the old key dies the moment the env var changes.
   b. Put the new key only in the local, gitignored `.env` (`LOFI_API_KEY=...`) — `.lofi` reads it from the environment; no secret lives in a committed file.
   c. Re-copy `.lofi` to any machine whose local copy still hardcodes the key (the committed line 63 is now `${LOFI_API_KEY:-}`).
   d. Scrub the old key from git history with `git filter-repo` + force push (same procedure used for the worker URL).

---

## Open Issues / Risks

| # | Issue | Status |
|---|-------|--------|
| 1 | Datacenter IPs don't get audio-only rungs from YouTube | **Resolved — Worker strips video at the edge; client gets audio-only** |
| 2 | Worker MPEG-TS → AAC extraction is simplified | **Resolved — PMT-based PID discovery + PES-correct extraction tested (81 KB clean AAC); TS-filter fallback in place** |
| 3 | `halloween-lofi` and `christmas-lofi` are seasonal | **Already handled — marked null/error when off-season** |
| 4 | Signed URL IP-binding (cross-IP gate) | **Proven — segments signed on the GitHub/Azure runner IP fetched fine from Cloudflare edge; client only talks to the Worker** |
| 5 | BFG history cleanup for API key | **Decoupled from teardown — rotate key → env var → scrub history (Priority 6)** |
| 6 | Node.js 20 deprecation warning in GitHub Actions | **Cosmetic — does not affect functionality** |
| 7 | Ubuntu 26.04 runner migration (Oct 19, 2026) | **Cosmetic warning, no action needed** |

---

## Key Files Reference

| File | Role |
|------|------|
| `tools/resolve_streams.py` | Standalone resolver — pulls Gist jar, runs yt-dlp per stream, emits `data/streams.json` |
| `.github/workflows/resolve-streams.yml` | 30-min cron + manual dispatch; installs Deno + yt-dlp + curl_cffi, commits document |
| `data/streams.json` | Published stream document; currently has muxed-fallback URLs from datacenter runner IP |
| `tests/test_resolver.py` | 9 tests, stubbed yt-dlp seam |
| `.lofi` | zsh client: 3 backends (worker/json/relay), cover art, cava, restart loop |
| `worker/index.ts` | Cloudflare Worker — audio-only proxy (deployed, verified 2026-10-15) |
| `worker/wrangler.toml` | Worker config |
| `app/config.py` | Env config + `STREAM_LABELS` dict (22 canonical keys) |
| `docs/migration-setup.html` | Owner setup guide (Steps 1–6) |
| `README.md` | Project documentation |
| `ORIENT.md` | Session memo with architecture overview |

---

## Commands Cheat Sheet

```bash
# Trigger resolver manually
gh workflow run resolve-streams.yml -R Wealth1000/lofi-relay

# Watch the run
gh run watch --repo Wealth1000/lofi-relay

# Check the live document
curl -sSL https://raw.githubusercontent.com/Wealth1000/lofi-relay/master/data/streams.json | jq '{status, updated_at, fresh: ([.streams[] | select(.stale == false)] | length)}'

# Deploy the worker
cd worker && npm install && npx wrangler deploy

# Test worker health
curl -sSL https://lofi-relay.<name>.workers.dev/api/streams | jq '.status'

# Play a stream (worker backend)
export LOFI_WORKER_BASE=https://lofi-relay.<name>.workers.dev
lofi r   # relax-study
lofi -R  # random

# Switch backends
LOFI_BACKEND=json lofi r    # raw GitHub URL
LOFI_BACKEND=relay lofi r   # Render fallback
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions (30-min cron, rotating runner IPs)          │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────────────┐   │
│  │ cron     │→│ yt-dlp +     │→│ data/streams.json    │   │
│  │ */30     │  │ cookie jar   │  │ (public, committed)  │   │
│  │          │  │ Gist pull    │  │                      │   │
│  └──────────┘  └──────────────┘  └──────────┬──────────┘   │
└─────────────────────────────────────────────┼───────────────┘
                                              │
                                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Cloudflare Worker (free tier: 100k req/day)                │
│  ┌─────────────────┐    ┌──────────────────────────────┐    │
│  │ /api/streams    │    │ /api/play/<endpoint>         │    │
│  │ (cached 5 min)  │    │  → audio-only: pass through  │    │
│  └────────┬────────┘    │  → muxed: rewrite m3u8       │    │
│           │             └──────────────┬───────────────┘    │
│           │                            │                    │
│           │             ┌──────────────▼───────────────┐    │
│           │             │ /api/seg/<ep>?n=<seq>        │    │
│           │             │  → fetch muxed segment       │    │
│           │             │  → strip video (MPEG-TS→AAC) │    │
│           │             │  → return ADTS (~80 KB/seg)  │    │
│           │             └──────────────┬───────────────┘    │
│           │                            │                    │
│           └────────────┬───────────────┘                    │
└────────────────────────┼────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Client (.lofi, zsh)                                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ worker   │  │ json     │  │ relay    │  │ random   │   │
│  │ (default)│  │ (raw URL)│  │ (Render) │  │ (-R flag)│   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────┘   │
│       │             │             │                        │
│       └─────────────┴─────────────┘                        │
│                          │                                 │
│                          ▼                                 │
│                 mpv --no-video                              │
│                 cava (visualizer)                           │
│                 ~16 KB/s transfer (audio-only)              │
└─────────────────────────────────────────────────────────────┘
```

---

## Notes for Next Agent

1. **The main unsolved problem**: YouTube serves different HLS ladders based on IP type. Residential IPs get audio-only rungs (234/233). Datacenter IPs (GitHub Actions runners, Render) get muxed rungs (91/96). The Cloudflare Worker bridges this gap by sitting between the client and YouTube — the client talks to the Worker from its residential IP, so it gets audio-only segments.

2. **The worker's MPEG-TS parser is simplified**. It looks for PID 0x101 (audio) and 0x100 (video) based on ffprobe output from one stream. Other streams may use different PIDs. If playback fails, check the actual PID assignments with `ffprobe` on a fresh segment and update the parser.

3. **The `muxed-fallback` variant in `data/streams.json` is intentional** — it records what the runner actually got. The worker uses this field to decide whether to pass through or rewrite. Don't try to "fix" the resolver to get audio-only from the runner; it can't, and that's the whole point of the worker.

4. **Render is still live and functional**. Do not destroy it until the worker has been running cleanly for 7+ days and the second-IP proof has passed.

5. **The `halloween-lofi` and `christmas-lofi` entries will be `null`+error when off-season**. This is correct behavior — the resolver marks them `stale: true` and preserves the last-good URL. During their season, the resolver will publish fresh URLs.

6. **`JAR_GIST_ID` and `JAR_GITHUB_TOKEN` secrets are configured** in the repo (verified). The resolver uses them to pull the cookie jar on each run. Without these, all streams would be `stale: true`.
