# Migration Handoff — lofi-relay Off Render → Audio-Only via GitHub Actions + CF Worker

**Date**: 2026-10-14  
**Owner**: Wealth1000  
**Repo**: `Wealth1000/lofi-relay` (public, branch `master`)

---

## Status at Last Session

The migration off Render is **partially complete**. The resolver cron and client rewrite are live. The one remaining open question is whether YouTube's audio-only HLS rungs (itag 234/233) are accessible from a datacenter egress IP — they are **not**, which means the published `data/streams.json` currently contains **muxed-fallback** URLs (itag 96, ~520 MB/hour) instead of the target audio-only ones (~58 MB/hour).

A Cloudflare Worker solution was drafted but not yet deployed.

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
| `worker` (default) | Fetches document from CF Worker `/api/play/<endpoint>`, plays result directly via mpv | **Primary path once worker is deployed** |
| `json` | Fetches `data/streams.json` directly from GitHub raw, plays the raw URL | Debugging, or when your residential IP gets audio-only |
| `relay` | Falls back to Render relay at `https://lofi-relay.onrender.com/lofi/<endpoint>` | Emergency fallback; keeps keepalive pinger |

Key details:
- `LOFI_WORKER_BASE` env var points to the deployed Worker URL (e.g. `https://lofi-relay.<your-name>.workers.dev`). Not yet set — needs the Worker deployed first.
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

### 8. Cloudflare Worker — `worker/` 🟡 (drafted, NOT deployed)
Three files in `worker/`:
- `index.ts` — Hono-based Worker with 3 routes:
  - `GET /api/streams` — proxies the document from GitHub raw URL, cached 5 min
  - `GET /api/play/<endpoint>` — returns `{"url": "...", "variant": "..."}`; for audio-only streams passes through the signed URL directly; for muxed-fallback rewrites the m3u8 playlist so segment URLs route through `/api/seg/<endpoint>?n=<seq>`
  - `GET /api/seg/<endpoint>?n=<seq>` — downloads the muxed segment from YouTube, extracts AAC PES payload from MPEG-TS container (PID 0x101), wraps in ADTS, returns `audio/aac`
- `package.json` — dependencies: `hono`, dev deps: `wrangler`, `@cloudflare/workers-types`
- `wrangler.toml` — name `lofi-relay`, main `index.ts`, compatibility `2024-01-01`, `nodejs_compat` flag, cache rule for `/api/streams`

**Known limitation in the worker**: The MPEG-TS → AAC extraction uses a simplified PID 0x101 filter. If YouTube changes the PID or serves a different container format, the fallback returns the raw segment (with video). Test this before relying on it.

---

## What Still Needs to Be Done

### Priority 1 — Deploy the Cloudflare Worker
```bash
cd worker
npm install
npx wrangler login
npx wrangler deploy
```
After deployment, note the Workers subdomain (e.g. `lofi-relay.<name>.workers.dev`).

### Priority 2 — Set `LOFI_WORKER_BASE` on your machine
Add to `~/.zshrc` or `~/.zshenv`:
```zsh
export LOFI_WORKER_BASE="https://lofi-relay.<your-name>.workers.dev"
```

### Priority 3 — Verify the Worker Works
```bash
# Test the streams endpoint
curl -sSL https://lofi-relay.<your-name>.workers.dev/api/streams | jq '.status, .updated_at, ([.streams[] | select(.stale == false)] | length)'

# Test play endpoint for a muxed-fallback stream
curl -sSL "https://lofi-relay.<your-name>.workers.dev/api/play/relax-study" | jq '.variant, (.url | .[0:100])'

# Test a segment download — should be ~80 KB (audio-only), not ~730 KB (muxed)
URL=$(curl -sSL "https://lofi-relay.<your-name>.workers.dev/api/play/relax-study" | jq -r '.url')
# The returned URL should be the worker's rewritten m3u8
curl -sSL "$URL" | grep -oE 'https?://[^"]+\.ts' | head -1 | while read seg; do
  # Replace the segment URL with the worker's proxy
  proxy_seg=$(echo "$seg" | sed 's|/api/seg/|&|')  # actually just use the rewritten URL directly
done

# Easiest test: play via mpv
lofi r   # should play audio, download ~80KB/segment
```

### Priority 4 — Validate Transfer Cost
Monitor actual bandwidth during a playback session:
```bash
# In another terminal, watch network usage for the mpv process
watch -n 5 'ps aux | grep mpv | grep -v grep'
# Or use iftop/tc to measure bytes/sec
```
Expected: ~16 KB/s (130 kbps AAC) for audio-only. If you see ~150 KB/s, the video is still coming through (worker segment proxy may have a bug).

### Priority 5 — Run the Second-IP Proof (the gate)
Once the worker is confirmed working from your residential IP:
```bash
# Step A: curl the raw document and verify it's accessible
curl -sSL https://raw.githubusercontent.com/Wealth1000/lofi-relay/master/data/streams.json \
  | jq '.streams["relax-study"] | {variant, format_id}'

# Step B: ffprobe a segment to confirm audio-only
URL=$(curl -sSL https://raw.githubusercontent.com/Wealth1000/lofi-relay/master/data/streams.json \
  | jq -r '.streams["relax-study"].url')
# Download one segment and probe
curl -sSL "$URL" | grep -oE 'https://[^"]+\.ts' | head -1 | xargs -I{} curl -sL '{}' -o /tmp/test.ts
ffprobe -hide_banner -show_entries stream=codec_type /tmp/test.ts 2>&1 | grep codec_type
# Expected output: only "audio" (no "video")

# Step C: 30-second mpv play test
mpv --no-video --really-quiet --cache=yes "$(curl -sSL https://lofi-relay.<your-name>.workers.dev/api/play/relax-study | jq -r '.url')"
# Should play audio for 30s without error, then exit
```
If all three pass → **gate passes** → Render can be torn down after 7 clean days.

### Priority 6 — After 7 Clean Days, Teardown Render
1. Confirm the resolver cron has been publishing fresh audio-only URLs for 7+ days.
2. Confirm `lofi -health` shows all streams playable via worker.
3. Stop the Render instance (Settings → Destroy this application).
4. Remove the `X-API-Key` from `.lofi` and rewrite the relay fallback path to be truly dead.
5. Run BFG Repo-Cleaner to remove the hardcoded API key from git history (separate task).

---

## Open Issues / Risks

| # | Issue | Status |
|---|-------|--------|
| 1 | Datacenter IPs don't get audio-only rungs from YouTube | **Root cause identified, worker should fix** |
| 2 | Worker MPEG-TS → AAC extraction is simplified | **Needs testing; fallback returns raw segment if PID 0x101 not found** |
| 3 | `halloween-lofi` and `christmas-lofi` are seasonal | **Already handled — marked null/error when off-season** |
| 4 | Signed URL IP-binding (cross-IP gate) | **Still unproven from residential IP; this is the main risk** |
| 5 | BFG history cleanup for API key | **Deferred — separate task after Render teardown** |
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
| `worker/index.ts` | Cloudflare Worker draft — audio-only proxy (NOT deployed) |
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
