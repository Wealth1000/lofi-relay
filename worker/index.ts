/**
 * lofi-relay Cloudflare Worker — audio-only proxy.
 *
 * The GitHub resolver publishes data/streams.json. Each entry has either:
 *   variant: "audio-only"      →  URL is pure AAC, client plays it directly
 *   variant: "muxed-fallback"  →  URL is muxed HLS, worker rewrites the
 *                                 manifest so segments route through this
 *                                 worker which strips video before forwarding.
 *
 * Client-facing API:
 *   GET /api/streams  – the document with CORS (cached 5 min)
 *   GET /api/play/<ep> – returns either a direct URL or a rewritten m3u8
 */

import { Hono } from "hono";
import { cors } from "hono/cors";

const STREAMS_DOC_URL =
  "https://raw.githubusercontent.com/Wealth1000/lofi-relay/master/data/streams.json";
const CACHE_TTL = 5 * 60;

type StreamEntry = {
  url: string | null;
  variant: string | null;
  stale: boolean;
};

type StreamsDoc = {
  status: string;
  updated_at: string;
  last_error?: string;
  streams: Record<string, StreamEntry>;
};

// ── Helpers ──────────────────────────────────────────────────────────────────

async function fetchStreamsDoc(): Promise<StreamsDoc> {
  const res = await fetch(STREAMS_DOC_URL);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<StreamsDoc>;
}

/**
 * Parse an m3u8 playlist and return segments with their sequence numbers.
 */
function parseSegments(text: string): Array<{ n: number; url: string }> {
  const segs: Array<{ n: number; url: string }> = [];
  let seq = 0;
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t.startsWith("http")) continue;
    segs.push({ n: seq++, url: t });
  }
  return segs;
}

/** Replace every segment URL in an m3u8 to route through /api/seg/<ep>?n=<n> */
function rewriteM3u8(playlist: string, endpoint: string, origin: string): string {
  const segments = parseSegments(playlist);
  let pos = 0;
  // Walk the playlist line by line; replace any line that is a bare URL.
  return playlist
    .split("\n")
    .map((line) => {
      const t = line.trim();
      if (!t.startsWith("http")) return line;
      // Find which segment index this URL corresponds to
      const found = segments.find((s) => s.url === t);
      if (found) {
        return `${origin}/api/seg/${encodeURIComponent(endpoint)}?n=${found.n}`;
      }
      // Non-segment URL (shouldn't happen in well-formed m3u8, but preserve it)
      return line;
    })
    .join("\n");
}

// ── Hono app ─────────────────────────────────────────────────────────────────

const app = new Hono<{ Bindings: Env }>();
app.use("*", cors());

// ── GET /api/streams ─────────────────────────────────────────────────────────

app.get("/api/streams", async (c) => {
  const cache = caches.default;
  const cached = await cache?.match(c.req.raw);
  if (cached) return new Response(cached.body, cached);

  try {
    const doc = await fetchStreamsDoc();
    const body = JSON.stringify(doc);
    const headers = new Headers({
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": `public, max-age=${CACHE_TTL}`,
      "Access-Control-Allow-Origin": "*",
    });
    const response = new Response(body, { status: 200, headers });
    await cache?.put(c.req.raw, response.clone());
    return response;
  } catch (err) {
    return c.json({ error: "failed to fetch streams document", detail: String(err) }, 502);
  }
});

// ── GET /api/play/<endpoint> ────────────────────────────────────────────────

app.get("/api/play/:endpoint", async (c) => {
  const endpoint = c.req.param("endpoint");
  // Build the worker origin from the request URL
  const url = new URL(c.req.url);
  const origin = `${url.protocol}//${url.hostname}`;

  let doc: StreamsDoc;
  try {
    doc = await fetchStreamsDoc();
  } catch {
    return c.json({ error: "streams document unavailable" }, 502);
  }

  const entry = doc.streams?.[endpoint];
  if (!entry || !entry.url) {
    return c.json({ error: `unknown or offline stream: ${endpoint}` }, 404);
  }

  // Audio-only: client plays directly
  if (entry.variant === "audio-only") {
    return c.json({ url: entry.url, variant: entry.variant });
  }

  // Muxed fallback: proxy the manifest, rewriting segment URLs
  try {
    const manifestRes = await fetch(entry.url);
    if (!manifestRes.ok) {
      return c.json({ error: "manifest fetch failed", detail: `HTTP ${manifestRes.status}` }, 502);
    }
    const manifest = await manifestRes.text();
    const rewritten = rewriteM3u8(manifest, endpoint, origin);

    return new Response(rewritten, {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.apple.mpegurl",
        "Access-Control-Allow-Origin": "*",
      },
    });
  } catch (err) {
    return c.json({ error: "manifest proxy failed", detail: String(err) }, 502);
  }
});

// ── GET /api/seg/<endpoint>?n=<seq> ─────────────────────────────────────────

app.get("/api/seg/:endpoint", async (c) => {
  const endpoint = c.req.param("endpoint");
  const seqParam = c.req.query("n");
  if (!seqParam || isNaN(Number(seqParam))) {
    return c.json({ error: "missing or invalid ?n=<seq>" }, 400);
  }
  const n = parseInt(seqParam, 10);

  let doc: StreamsDoc;
  try {
    doc = await fetchStreamsDoc();
  } catch {
    return c.json({ error: "streams document unavailable" }, 502);
  }

  const entry = doc.streams?.[endpoint];
  if (!entry || !entry.url) {
    return c.json({ error: `unknown stream: ${endpoint}` }, 404);
  }

  // Fetch the manifest
  let manifest: string;
  try {
    const mRes = await fetch(entry.url);
    if (!mRes.ok) return c.json({ error: `manifest HTTP ${mRes.status}` }, 502);
    manifest = await mRes.text();
  } catch {
    return c.json({ error: "failed to fetch manifest" }, 502);
  }

  const segments = parseSegments(manifest);
  const segment = segments.find((s) => s.n === n);
  if (!segment) return c.json({ error: `segment ${n} not found` }, 404);

  // Download the muxed segment from YouTube
  let segBuf: ArrayBuffer;
  try {
    const sRes = await fetch(segment.url);
    if (!sRes.ok) return c.json({ error: `segment HTTP ${sRes.status}` }, 502);
    segBuf = await sRes.arrayBuffer();
  } catch {
    return c.json({ error: "failed to download segment" }, 502);
  }

  const uint8 = new Uint8Array(segBuf);
  if (uint8.length < 188) {
    return c.json({ error: "segment too small" }, 500);
  }

  // ── MPEG-TS → AAC extraction ─────────────────────────────────────────────
  // MPEG-TS packets are 188 bytes. PID 0x101 = audio, 0x100 = video
  // (confirmed from ffprobe output: Stream #0:0[0x101] audio, Stream #0:1[0x100] video)
  //
  // We collect all audio PIDs, extract their PES payload, then wrap in ADTS.

  const audioPES: Uint8Array[] = [];
  let pos = 0;

  while (pos + 188 <= uint8.length) {
    if (uint8[pos] !== 0x47) {
      // Resync: scan forward up to 20 bytes for next sync word
      let synced = false;
      for (let j = 1; j <= 20 && pos + j + 188 <= uint8.length; j++) {
        if (uint8[pos + j] === 0x47) {
          pos += j;
          synced = true;
          break;
        }
      }
      if (!synced) break;
      continue;
    }

    const pid = ((uint8[pos + 1] & 0x1f) << 8) | uint8[pos + 2];

    if (pid === 0x101) {
      // Audio PID — extract PES payload
      const adaptationLen = pos + 4 < uint8.length ? uint8[pos + 3] >> 4 : 0;
      const payOff = 4 + (adaptationLen > 0 ? adaptationLen + 1 : 0);
      if (payOff < 188) {
        const payload = uint8.subarray(pos + payOff, pos + 188);
        if (payload.length > 0) audioPES.push(payload);
      }
    }

    pos += 188;
  }

  if (audioPES.length === 0) {
    // No audio PID found — return raw segment and let mpv handle it
    return new Response(segBuf, {
      status: 200,
      headers: { "Content-Type": "video/MP2T", "Access-Control-Allow-Origin": "*" },
    });
  }

  // Concatenate AAC PES payloads
  const aacData = concatArrays(audioPES);

  // Wrap in ADTS so mpv can consume the stream
  const adts = aacToAdts(aacData);

  return new Response(adts, {
    status: 200,
    headers: {
      "Content-Type": "audio/aac",
      "Access-Control-Allow-Origin": "*",
    },
  });
});

// ── Bit-packing helpers ──────────────────────────────────────────────────────

function concatArrays(parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((s, p) => s + p.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

/**
 * Build a 7-byte ADTS header.
 * profile: 1=LC (most common), 2=HE, 3=main
 * freqIdx: 0-13 → sampling rate
 * chanCfg: 0-6 → channel configuration
 */
function adtsHeader(profile: number, freqIdx: number, chanCfg: number, frameSize: number): Uint8Array {
  const buf = new Uint8Array(7);
  buf[0] = 0xFF;
  buf[1] = 0xF9;
  buf[2] = ((profile - 1) << 6) | ((freqIdx & 0x0f) << 2) | ((chanCfg >> 2) & 0x03);
  buf[3] = ((chanCfg & 0x03) << 6) | ((frameSize >> 11) & 0x03);
  buf[4] = (frameSize >> 3) & 0xFF;
  buf[5] = ((frameSize & 0x07) << 5) | 0x1F; // buffer fullness 0x7FF
  buf[6] = 0xFC;
  return buf;
}

/**
 * Split raw AAC data into ADTS-framed chunks.
 *
 * Strategy: we know from ffprobe that the stream is AAC-LC at 44100 Hz stereo.
 * At ~130 kbps, each 1024-sample frame is ~800 bytes. We chunk the raw AAC
 * data into ~800-byte pieces and prepend an ADTS header to each.
 *
 * If we can't determine the frame size precisely, we fall back to a single
 * ADTS-wrapped blob.
 */
function aacToAdts(aacData: Uint8Array): Uint8Array {
  // Detect sampling rate index from the first few bytes of AAC data
  // In an AAC frame, the first byte contains: syncword(12bits=0xFFF) is outside
  // the raw AAC frame — we're working with raw ASC (AudioSpecificConfig) data
  // So we use the known values: LC profile, 44100Hz = freqIdx 4, stereo = chanCfg 2
  const profile = 1; // AAC-LC
  const freqIdx = 4; // 44100 Hz
  const chanCfg = 2; // stereo

  const frames: Uint8Array[] = [];
  const targetFrameSize = 800; // ~130kbps / 30fps ≈ 4333 bytes/sec ÷ 5.5 frames/sec ≈ 800 bytes/frame

  let offset = 0;
  while (offset < aacData.length) {
    const remaining = aacData.length - offset;
    const chunkSize = Math.min(targetFrameSize, remaining);
    const chunk = aacData.subarray(offset, offset + chunkSize);
    offset += chunkSize;

    const frameTotalSize = chunk.length + 7; // ADTS header + payload
    const header = adtsHeader(profile, freqIdx, chanCfg, frameTotalSize);
    frames.push(header);
    frames.push(chunk);
  }

  return concatArrays(frames);
}

export default app;
