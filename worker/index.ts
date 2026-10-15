/**
 * lofi-relay Cloudflare Worker — audio-only proxy.
 *
 * data/streams.json (published by the GitHub resolver) gives every stream's
 * signed m3u8. Because the resolver runs on a datacenter IP, YouTube hands it
 * the muxed ladder (audio + 1080p video ≈ 740 KB per 5 s segment). This
 * worker sits between the client and YouTube:
 *
 *   client (residential IP) ─▶ worker (CF edge) ─▶ YouTube CDN
 *
 * and strips the video out of every segment on the way back, so the client's
 * line carries audio only (~80 KB per 5 s ≈ 130 kb/s).
 *
 * Client API — /api/play always returns JSON {"url", "variant"}:
 *   GET /api/streams            the resolver document, edge-cached 5 min
 *   GET /api/play/<endpoint>    audio-only → the signed URL plays directly
 *                               muxed      → url = /api/m3u8/<endpoint>
 *   GET /api/m3u8/<endpoint>    the manifest with every segment URL rewritten
 *                               to /api/seg/<endpoint>?n=<media-sequence>
 *   GET /api/seg/<endpoint>?n=  one segment, video stripped (audio/aac)
 *
 * Segment keys are ABSOLUTE media-sequence numbers, not window positions: the
 * live manifest rolls forward every ~5 s, so a positional index resolved at
 * segment-fetch time would silently point at a different segment than the
 * playlist the player read.
 */

import { Hono } from "hono";
import { cors } from "hono/cors";

const STREAMS_DOC_URL =
  "https://raw.githubusercontent.com/Wealth1000/lofi-relay/master/data/streams.json";
const DOC_MEM_TTL = 60_000;
const DOC_EDGE_TTL = 5 * 60;

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

// ── streams document ─────────────────────────────────────────────────────────

// 60 s in-memory cache: every /api/seg and /api/m3u8 request needs the entry
// URL, and hitting GitHub raw for each one would dwarf the YouTube traffic.
// Isolates are recycled often enough that this never goes properly stale, and
// a minutes-old entry is still playable for hours anyway.
let docMem: { doc: StreamsDoc; at: number } | null = null;

async function fetchStreamsDoc(): Promise<StreamsDoc> {
  const now = Date.now();
  if (docMem && now - docMem.at < DOC_MEM_TTL) return docMem.doc;
  try {
    const res = await fetch(STREAMS_DOC_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const doc = (await res.json()) as StreamsDoc;
    docMem = { doc, at: now };
    return doc;
  } catch (err) {
    if (docMem) return docMem.doc;
    throw err;
  }
}

// ── manifest parse / rewrite ─────────────────────────────────────────────────

type Manifest = { mediaSeq: number; segments: string[] };

function parseManifest(text: string): Manifest {
  let mediaSeq = 0;
  const segments: string[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line.startsWith("#EXT-X-MEDIA-SEQUENCE:")) {
      const v = parseInt(line.slice("#EXT-X-MEDIA-SEQUENCE:".length), 10);
      if (!Number.isNaN(v)) mediaSeq = v;
    } else if (line.length > 0 && !line.startsWith("#")) {
      segments.push(line);
    }
  }
  return { mediaSeq, segments };
}

/** Replace every segment URI with an absolute-sequence worker URL. */
function rewriteManifest(text: string, endpoint: string, origin: string): string {
  const { mediaSeq } = parseManifest(text);
  let i = 0;
  return text
    .split("\n")
    .map((raw) => {
      const line = raw.trim();
      if (line.length === 0 || line.startsWith("#")) return raw;
      return `${origin}/api/seg/${encodeURIComponent(endpoint)}?n=${mediaSeq + i++}`;
    })
    .join("\n");
}

// ── MPEG-TS ──────────────────────────────────────────────────────────────────

const TS_PACKET = 188;
const SYNC_BYTE = 0x47;

type TsWalk = { pid: number; pusi: boolean; payload: Uint8Array | null };

/**
 * Walk 188-byte TS packets. `payload` is the packet payload after the TS
 * header and any adaptation field (null for adaptation-only packets).
 */
function* walkTs(u8: Uint8Array): Generator<TsWalk> {
  let pos = 0;
  while (pos + TS_PACKET <= u8.length) {
    if (u8[pos] !== SYNC_BYTE) {
      let next = -1;
      for (let j = pos + 1; j + TS_PACKET <= u8.length; j++) {
        if (u8[j] === SYNC_BYTE && u8[j + TS_PACKET] === SYNC_BYTE) {
          next = j;
          break;
        }
      }
      if (next < 0) return;
      pos = next;
      continue;
    }
    const b1 = u8[pos + 1];
    const b3 = u8[pos + 3];
    const pusi = (b1 & 0x40) !== 0;
    const pid = ((b1 & 0x1f) << 8) | u8[pos + 2];
    const afc = (b3 >> 4) & 0x03; // adaptation_field_control
    let payload: Uint8Array | null = null;
    if (afc === 1 || afc === 3) {
      let off = 4;
      if (afc === 3) off = 5 + u8[pos + 4]; // skip adaptation field + its length byte
      if (off < TS_PACKET) payload = u8.subarray(pos + off, pos + TS_PACKET);
    }
    yield { pid, pusi, payload };
    pos += TS_PACKET;
  }
}

/** A PSI section (PAT/PMT) payload: skip pointer_field, bound by section_length. */
function psiSection(payload: Uint8Array): Uint8Array | null {
  if (payload.length < 4) return null;
  const start = 1 + payload[0]; // pointer_field
  if (start + 3 > payload.length) return null;
  const sectionLength = ((payload[start + 1] & 0x0f) << 8) | payload[start + 2];
  const end = Math.min(start + 3 + sectionLength, payload.length);
  return payload.subarray(start, end);
}

const AUDIO_STREAM_TYPES = new Set([
  0x03, 0x04, 0x0f, 0x11, 0x6a, 0x81, 0x87, 0x8a, 0x90, 0x91,
]);
const VIDEO_STREAM_TYPES = new Set([0x01, 0x02, 0x10, 0x1b, 0x24, 0x42, 0xea]);

type Pids = { audioPid: number; audioType: number | null; videoPid: number | null };

/**
 * PAT → PMT → elementary-stream pids. PIDs are NOT constant across streams
 * (audio happens to be 0x101 on today's ladder, but the PMT is the only
 * authority). Returns null when the segment carries no parseable PAT/PMT.
 */
function discoverPids(u8: Uint8Array): Pids | null {
  const pmtPids: number[] = [];
  for (const { pid, pusi, payload } of walkTs(u8)) {
    if (pid !== 0 || !pusi || !payload) continue;
    const s = psiSection(payload);
    if (!s || s[0] !== 0x00 || s.length < 8) continue;
    for (let i = 8; i + 4 <= s.length; i += 4) {
      const program = (s[i] << 8) | s[i + 1];
      if (program === 0) continue; // network PID, not a PMT
      pmtPids.push(((s[i + 2] & 0x1f) << 8) | s[i + 3]);
    }
    break; // one PAT is enough
  }

  for (const pmtPid of pmtPids) {
    for (const { pid, pusi, payload } of walkTs(u8)) {
      if (pid !== pmtPid || !pusi || !payload) continue;
      const s = psiSection(payload);
      if (!s || s[0] !== 0x02 || s.length < 12) break;
      const infoLen = ((s[10] & 0x0f) << 8) | s[11];
      let audioPid = -1;
      let audioType: number | null = null;
      let videoPid = -1;
      let i = 12 + infoLen;
      while (i + 5 <= s.length) {
        const st = s[i];
        const epid = ((s[i + 1] & 0x1f) << 8) | s[i + 2];
        const esInfoLen = ((s[i + 3] & 0x0f) << 8) | s[i + 4];
        if (audioPid < 0 && AUDIO_STREAM_TYPES.has(st)) {
          audioPid = epid;
          audioType = st;
        }
        if (videoPid < 0 && VIDEO_STREAM_TYPES.has(st)) videoPid = epid;
        i += 5 + esInfoLen;
      }
      if (audioPid >= 0) {
        return { audioPid, audioType, videoPid: videoPid >= 0 ? videoPid : null };
      }
      break;
    }
  }
  return null;
}

/**
 * Pull the ADTS AAC byte stream out of a muxed MPEG-TS segment.
 *
 * Audio PES payloads are already ADTS-framed by YouTube — no re-wrapping is
 * needed (or safe: faking ADTS headers over unframed bytes is what made the
 * first version undecodable). All we do is remove the PES headers, which
 * requires honoring the adaptation field and the payload-unit-start bit.
 *
 * Returns null if the structure isn't what we expect — caller falls back to
 * filtering video packets instead of handing the client garbage.
 */
function extractAdts(u8: Uint8Array, audioPid: number): Uint8Array | null {
  const pusiOffsets: number[] = [];
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (const { pid, pusi, payload } of walkTs(u8)) {
    if (pid !== audioPid || !payload || payload.length === 0) continue;
    if (pusi) pusiOffsets.push(total);
    chunks.push(payload);
    total += payload.length;
  }
  if (pusiOffsets.length === 0) return null;

  // Concatenate payloads: a PES packet (and even its header) may span TS
  // packets, so parse the PES layer over the flattened stream, not per packet.
  const buf = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    buf.set(c, off);
    off += c.length;
  }

  const parts: Uint8Array[] = [];
  for (let k = 0; k < pusiOffsets.length; k++) {
    const p = pusiOffsets[k];
    if (p + 9 > buf.length) break;
    if (buf[p] !== 0x00 || buf[p + 1] !== 0x00 || buf[p + 2] !== 0x01) {
      return null; // not a PES start — wrong pid or corrupt input
    }
    const sid = buf[p + 3];
    // Stream ids that carry no optional PES header fields.
    const noOptionalHeader =
      sid === 0xbc || sid === 0xbe || sid === 0xbf || sid === 0xf0 ||
      sid === 0xf1 || sid === 0xf2 || sid === 0xf8 || sid === 0xff;
    const payloadStart = noOptionalHeader ? p + 6 : p + 9 + buf[p + 8];
    const pesLen = (buf[p + 4] << 8) | buf[p + 5];
    const next = pusiOffsets[k + 1] ?? buf.length;
    // pesLen counts bytes after the length field; PES without a length
    // (allowed for video, not audio) runs until the next start code.
    const pesEnd = pesLen > 0 ? Math.min(p + 6 + pesLen, next) : next;
    if (payloadStart < pesEnd) parts.push(buf.subarray(payloadStart, pesEnd));
  }
  if (parts.length === 0) return null;

  let size = 0;
  for (const part of parts) size += part.length;
  const out = new Uint8Array(size);
  let o = 0;
  for (const part of parts) {
    out.set(part, o);
    o += part.length;
  }
  return out;
}

/** ADTS sync word 0xFFF in the first frame — cheap sanity check. */
function hasAdtsSync(b: Uint8Array): boolean {
  const n = Math.max(0, Math.min(b.length - 1, 64));
  for (let i = 0; i < n; i++) {
    if (b[i] === 0xff && (b[i + 1] & 0xf0) === 0xf0) return true;
  }
  return false;
}

/** Keep whole packets whose pid is not dropped. */
function filterTs(u8: Uint8Array, drop: Set<number>): Uint8Array | null {
  const out = new Uint8Array(u8.length);
  let w = 0;
  let count = 0;
  let pos = 0;
  while (pos + TS_PACKET <= u8.length) {
    if (u8[pos] !== SYNC_BYTE) {
      pos++;
      continue;
    }
    const pid = ((u8[pos + 1] & 0x1f) << 8) | u8[pos + 2];
    if (!drop.has(pid)) {
      out.set(u8.subarray(pos, pos + TS_PACKET), w);
      w += TS_PACKET;
      count++;
    }
    pos += TS_PACKET;
  }
  if (count === 0) return null;
  return out.subarray(0, w);
}

type Stripped = { body: Uint8Array; type: string };

/**
 * Primary path: extract ADTS audio (smallest, ~80 KB/segment).
 * Fallback: drop video packets and return the rest as MPEG-TS (keeps PTS
 * timing intact; mpv's --no-video doesn't care that the PMT still declares a
 * video track that never delivers packets).
 * Null: give up so the route can 502 instead of sending undecodable bytes.
 */
function stripVideo(segBuf: ArrayBuffer): Stripped | null {
  const u8 = new Uint8Array(segBuf);
  if (u8.length < TS_PACKET * 2 || u8[0] !== SYNC_BYTE) return null;

  const pids = discoverPids(u8);
  const audioPid = pids?.audioPid ?? 0x101; // YouTube's conventional audio PID
  const wantAdts = !pids || pids.audioType === null || pids.audioType === 0x0f;

  if (wantAdts) {
    const adts = extractAdts(u8, audioPid);
    if (adts && adts.length > 0 && hasAdtsSync(adts)) {
      return { body: adts, type: "audio/aac" };
    }
  }

  if (pids && pids.videoPid !== null) {
    const ts = filterTs(u8, new Set([pids.videoPid, 0x1fff])); // also drop null packets
    if (ts && ts.length > 0) return { body: ts, type: "video/MP2T" };
  }
  return null;
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
      "Cache-Control": `public, max-age=${DOC_EDGE_TTL}`,
      "Access-Control-Allow-Origin": "*",
    });
    const response = new Response(body, { status: 200, headers });
    await cache?.put(c.req.raw, response.clone());
    return response;
  } catch (err) {
    return c.json({ error: "failed to fetch streams document", detail: String(err) }, 502);
  }
});

// ── GET /api/play/<endpoint> ─────────────────────────────────────────────────
// Always JSON — .lofi parses `.url` unconditionally for every variant.

app.get("/api/play/:endpoint", async (c) => {
  const endpoint = c.req.param("endpoint");
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

  if (entry.variant === "audio-only") {
    return c.json({ url: entry.url, variant: entry.variant });
  }
  return c.json({
    url: `${origin}/api/m3u8/${encodeURIComponent(endpoint)}`,
    variant: entry.variant ?? "muxed-fallback",
  });
});

// ── GET /api/m3u8/<endpoint> ─────────────────────────────────────────────────

app.get("/api/m3u8/:endpoint", async (c) => {
  const endpoint = c.req.param("endpoint");
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

  try {
    const manifestRes = await fetch(entry.url);
    if (!manifestRes.ok) {
      return c.json({ error: "manifest fetch failed", detail: `HTTP ${manifestRes.status}` }, 502);
    }
    const manifest = await manifestRes.text();
    const rewritten = rewriteManifest(manifest, endpoint, origin);

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

// ── GET /api/seg/<endpoint>?n=<media-sequence> ───────────────────────────────

app.get("/api/seg/:endpoint", async (c) => {
  const endpoint = c.req.param("endpoint");
  const n = Number.parseInt(c.req.query("n") ?? "", 10);
  if (!Number.isFinite(n)) {
    return c.json({ error: "missing or invalid ?n=<media-sequence>" }, 400);
  }

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

  let manifestText: string;
  try {
    const mRes = await fetch(entry.url);
    if (!mRes.ok) return c.json({ error: `manifest HTTP ${mRes.status}` }, 502);
    manifestText = await mRes.text();
  } catch {
    return c.json({ error: "failed to fetch manifest" }, 502);
  }

  const { mediaSeq, segments } = parseManifest(manifestText);
  const idx = n - mediaSeq;
  if (idx < 0 || idx >= segments.length) {
    // The window rolled past this segment. Live players re-read the playlist
    // on failure, so a 404 here heals; serving a wrong-index segment would not.
    return c.json({ error: "segment outside the live window", n, mediaSeq }, 404);
  }

  let segBuf: ArrayBuffer;
  try {
    const sRes = await fetch(segments[idx]);
    if (!sRes.ok) return c.json({ error: `segment HTTP ${sRes.status}` }, 502);
    segBuf = await sRes.arrayBuffer();
  } catch {
    return c.json({ error: "failed to download segment" }, 502);
  }

  const stripped = stripVideo(segBuf);
  if (!stripped) {
    return c.json({ error: "could not extract audio from segment" }, 502);
  }

  return new Response(stripped.body, {
    status: 200,
    headers: { "Content-Type": stripped.type, "Access-Control-Allow-Origin": "*" },
  });
});

export default app;
