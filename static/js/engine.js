// Browser-side processing: fetch the media streams the server resolved (directly when
// the source allows it, otherwise through the server's streaming relay) and mux /
// convert them locally with ffmpeg.wasm. The server never touches ffmpeg or disk.

export class FallbackError extends Error {
  constructor(message) { super(message); this.fallback = true; }
}
export class CancelledError extends Error {
  constructor() { super("Cancelled"); this.cancelled = true; }
}

const MIME = {
  mp4: "video/mp4", webm: "video/webm", mkv: "video/x-matroska", m4a: "audio/mp4",
  mp3: "audio/mpeg", opus: "audio/ogg", flac: "audio/flac", wav: "audio/wav", weba: "audio/webm",
  jpg: "image/jpeg", webp: "image/webp", png: "image/png",
};
const sleep = (ms, signal) => new Promise((res, rej) => {
  const t = setTimeout(res, ms);
  signal?.addEventListener("abort", () => { clearTimeout(t); rej(new CancelledError()); }, { once: true });
});

export function browserCapable() {
  try {
    return typeof WebAssembly === "object" && typeof WebAssembly.instantiate === "function" &&
      typeof Worker === "function" && typeof Blob === "function" && "stream" in Blob.prototype &&
      typeof ReadableStream === "function";
  } catch { return false; }
}

export function prefersBrowser() {
  if (!browserCapable()) return false;
  const c = navigator.connection;
  if (c && (c.saveData || /(^|-)(2g|3g)$/.test(c.effectiveType || ""))) return false;
  if (navigator.deviceMemory && navigator.deviceMemory < 4) return false;
  return true;
}

// ── ffmpeg host ─────────────────────────────────────────────────────────
let compiled = null;   // Promise<WebAssembly.Module | null>

function compileWasm(url) {
  if (!compiled) {
    compiled = (async () => {
      try {
        if (WebAssembly.compileStreaming) {
          try { return await WebAssembly.compileStreaming(fetch(url, { credentials: "omit" })); }
          catch { /* wrong MIME type: fall through */ }
        }
        const buf = await (await fetch(url, { credentials: "omit" })).arrayBuffer();
        return await WebAssembly.compile(buf);
      } catch { compiled = null; return null; }
    })();
  }
  return compiled;
}

class FFmpegHost {
  constructor(core) {
    this.coreURL = new URL(core.js, location.href).href;
    this.wasmURL = new URL(core.wasm, location.href).href;
    this.pending = new Map();
    this.seq = 0;
    this.onProgress = null;
    this.logs = [];
    this.lastActivity = performance.now();
  }

  async start() {
    const mod = await compileWasm(this.wasmURL);
    this.worker = new Worker(new URL("./ffmpeg-worker.js", import.meta.url), { type: "module" });
    this.worker.onmessage = ({ data }) => {
      this.lastActivity = performance.now();
      if (data.type === "log") {
        this.logs.push(data.payload.message);
        if (this.logs.length > 60) this.logs.shift();
        return;
      }
      if (data.type === "progress") { this.onProgress?.(data.payload); return; }
      const p = this.pending.get(data.id);
      if (!p) return;
      this.pending.delete(data.id);
      data.type === "ok" ? p.resolve(data.payload) : p.reject(new Error(data.payload));
    };
    this.worker.onerror = (e) => {
      e.preventDefault?.();
      this.fail(new FallbackError("This browser couldn't run the converter (it may have run out of memory)."));
    };
    try {
      await this.call("load", { coreURL: this.coreURL, wasmURL: this.wasmURL, wasmModule: mod });
    } catch (e) {
      this.terminate();
      throw new FallbackError(`Couldn't load the in-browser converter: ${e.message}`);
    }
  }

  call(type, payload) {
    return new Promise((resolve, reject) => {
      const id = ++this.seq;
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, type, payload });
    });
  }

  /** Run ffmpeg, aborting if it produces no output for `stallMs` (a hung converter). */
  async exec(args, stallMs = 45000) {
    this.lastActivity = performance.now();
    let timer;
    const stalled = new Promise((_, reject) => {
      timer = setInterval(() => {
        if (performance.now() - this.lastActivity > stallMs) {
          reject(new FallbackError("The in-browser converter stopped responding."));
          this.terminate();
        }
      }, 2000);
    });
    try { return await Promise.race([this.call("exec", { args }), stalled]); }
    finally { clearInterval(timer); }
  }

  fail(err) {
    for (const p of this.pending.values()) p.reject(err);
    this.pending.clear();
  }

  terminate() {
    try { this.worker?.terminate(); } catch { /* ignore */ }
    this.fail(new CancelledError());
  }
}

// ── fetching ────────────────────────────────────────────────────────────
class HTTPError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function fetchRange(url, start, end, { signal, direct, onBytes }) {
  const init = { signal, cache: "no-store", headers: { Range: `bytes=${start}-${end}` } };
  if (direct) Object.assign(init, { mode: "cors", credentials: "omit", referrerPolicy: "no-referrer" });
  else init.credentials = "same-origin";
  const res = await fetch(url, init);
  if (res.status === 416) return { status: 416, blob: new Blob([]), total: null };
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); if (j.error) msg = j.error; } catch { /* not json */ }
    throw new HTTPError(res.status, msg);
  }
  const reader = res.body.getReader();
  const parts = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    n += value.byteLength;
    onBytes(n);
  }
  const cr = res.headers.get("Content-Range");
  const total = cr && /\/(\d+)\s*$/.test(cr) ? Number(cr.match(/\/(\d+)\s*$/)[1]) : null;
  return { status: res.status, blob: new Blob(parts), total };
}

async function fetchFrom(src, stream, signal, onBytes) {
  const chunk = Math.max(1 << 20, stream.chunk || 8 << 20);
  let total = stream.exact_size ? stream.size : null;
  let parts = [];
  let pos = 0;
  while (total === null || pos < total) {
    const want = total === null ? chunk : Math.min(chunk, total - pos);
    const end = pos + want - 1;
    let r;
    for (let attempt = 0; ; attempt++) {
      try {
        const base = pos;
        r = await fetchRange(src.url, pos, end, { signal, direct: src.direct, onBytes: (n) => onBytes(base + n) });
        break;
      } catch (e) {
        if (signal.aborted) throw new CancelledError();
        const fatal = e instanceof HTTPError && [400, 401, 403, 404, 410].includes(e.status);
        if (fatal || attempt >= 3 || (src.direct && pos === 0)) throw e;
        await sleep(1000 * 2 ** attempt, signal);
      }
    }
    if (r.status === 416) break;
    if (r.status === 200) { parts = [r.blob]; pos = r.blob.size; break; }  // server ignored Range
    parts.push(r.blob);
    pos += r.blob.size;
    if (r.total) total = r.total;
    if (r.blob.size === 0 || (total === null && r.blob.size < want)) break;
  }
  return new Blob(parts, { type: MIME[stream.ext] || "application/octet-stream" });
}

async function fetchStream(stream, signal, onBytes) {
  // YouTube stream URLs only work a few seconds after they were issued (yt-dlp's available_at).
  const waitMs = (stream.available_in || 0) * 1000 - (performance.now() - (stream.resolvedAt || 0)) + 500;
  if (stream.available_in > 0 && waitMs > 0) await sleep(Math.min(waitMs, 15000), signal);
  const sources = [];
  if (stream.direct) sources.push({ url: stream.direct, direct: true });
  if (stream.token) sources.push({ url: `api/relay/${encodeURIComponent(stream.token)}`, direct: false });
  if (!sources.length) throw new FallbackError("This source can't be downloaded from the browser.");
  let lastErr;
  for (const src of sources) {
    try {
      return await fetchFrom(src, stream, signal, onBytes);
    } catch (e) {
      if (signal.aborted || e.cancelled) throw new CancelledError();
      lastErr = e;
      onBytes(0);
      if (!src.direct) break;   // relay failed: nothing left to try
    }
  }
  if (lastErr instanceof HTTPError && [403, 410].includes(lastErr.status)) {
    // The site refused the relayed stream; the server can usually still fetch it with yt-dlp.
    throw new FallbackError(`The site refused to send this stream to the relay (${lastErr.message}).`);
  }
  throw new FallbackError(`Couldn't fetch the media in the browser: ${lastErr?.message || "unknown error"}`);
}

async function toJpeg(blob, maxSide = 1200) {
  const bmp = await createImageBitmap(blob);
  const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  const w = Math.max(1, Math.round(bmp.width * scale)), h = Math.max(1, Math.round(bmp.height * scale));
  let out;
  if (typeof OffscreenCanvas === "function") {
    const c = new OffscreenCanvas(w, h);
    c.getContext("2d").drawImage(bmp, 0, 0, w, h);
    out = await c.convertToBlob({ type: "image/jpeg", quality: 0.9 });
  } else {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    c.getContext("2d").drawImage(bmp, 0, 0, w, h);
    out = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.9));
  }
  bmp.close?.();
  return out;
}

// ── ffmpeg arguments ────────────────────────────────────────────────────
function needsFFmpeg(plan) {
  const s = plan.streams;
  const meta = Object.keys(plan.meta || {}).length > 0;
  if (s.length !== 1 || meta || plan.cover) return true;
  if (plan.kind === "video") return !(s[0].role === "av" && s[0].ext === plan.format);
  return !(plan.format === "m4a" && s[0].ext === "m4a" && s[0].acodec.startsWith("mp4a"));
}

function buildArgs(plan, inputs, coverPath, out) {
  const a = ["-hide_banner", "-nostdin", "-y"];
  for (const i of inputs) a.push("-i", i.path);
  const coverIdx = coverPath ? inputs.length : -1;
  if (coverPath) a.push("-i", coverPath);

  if (plan.kind === "video") {
    const v = Math.max(0, inputs.findIndex((i) => i.role !== "audio"));
    const au = inputs.findIndex((i) => i.role === "audio");
    a.push("-map", `${v}:v:0`, "-map", au >= 0 ? `${au}:a:0` : `${v}:a:0?`, "-c", "copy");
    if (plan.format === "mp4") a.push("-movflags", "+faststart");
  } else {
    const src = inputs[0];
    a.push("-map", "0:a:0");
    const br = `${plan.bitrate || 192}k`;
    switch (plan.format) {
      case "mp3": a.push("-c:a", "libmp3lame", "-b:a", br, "-id3v2_version", "3"); break;
      case "m4a": a.push(...(src.acodec.startsWith("mp4a") ? ["-c:a", "copy"] : ["-c:a", "aac", "-b:a", br]),
        "-movflags", "+faststart"); break;
      case "opus": a.push(...(src.acodec === "opus" ? ["-c:a", "copy"] : ["-c:a", "libopus", "-b:a", br])); break;
      case "flac": a.push("-c:a", "flac"); break;
      case "wav": a.push("-c:a", "pcm_s16le"); break;
    }
    if (coverIdx >= 0) {
      // The cover was converted to JPEG on the page already; re-encoding it inside the
      // single-threaded ffmpeg.wasm core deadlocks, so it is always stream-copied.
      a.push("-map", `${coverIdx}:v:0`, "-c:v", "copy", "-disposition:v:0", "attached_pic");
      if (plan.format === "mp3") a.push("-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)");
    } else {
      a.push("-vn");
    }
  }
  for (const [k, v] of Object.entries(plan.meta || {})) a.push("-metadata", `${k}=${v}`);
  a.push(out);
  return a;
}

// ── public API ──────────────────────────────────────────────────────────
export class BrowserEngine {
  constructor(config) {
    this.core = config.processing.ffmpeg_core;
    this.maxBytes = config.processing.browser_max_mb * 1024 * 1024;
    this.tail = Promise.resolve();
  }

  async resolve(req, signal) {
    const res = await fetch("api/resolve", {
      method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(req),
    });
    let data = {};
    try { data = await res.json(); } catch { /* empty */ }
    if (!res.ok) {
      if (data.fallback) throw new FallbackError(data.error);
      throw new Error(data.error || `Server error ${res.status}`);
    }
    const at = performance.now();
    for (const st of data.streams || []) st.resolvedAt = at;
    if (data.cover) data.cover.resolvedAt = at;
    return data;
  }

  /** Run jobs one after another so two big conversions never share memory. */
  enqueue(fn) {
    const run = this.tail.then(fn, fn);
    this.tail = run.catch(() => {});
    return run;
  }

  /**
   * Fetch + convert one resolved plan.
   * onState({stage, progress, speed, eta}) — progress 0..1
   */
  async process(plan, { signal, onState }) {
    if (plan.total_size && plan.total_size > this.maxBytes) {
      throw new FallbackError("This file is too large to process in the browser.");
    }
    const total = plan.streams.reduce((n, s) => n + (s.size || 0), 0) || null;
    const got = plan.streams.map(() => 0);
    let lastT = performance.now(), lastB = 0, speed = null;
    const report = (stage) => {
      const b = got.reduce((x, y) => x + y, 0);
      const now = performance.now();
      if (now - lastT > 700) {
        const inst = ((b - lastB) / (now - lastT)) * 1000;
        speed = speed == null ? inst : speed * 0.6 + inst * 0.4;
        lastT = now; lastB = b;
      }
      const frac = total ? Math.min(1, b / total) : null;
      const eta = total && speed > 0 ? (total - b) / speed : null;
      onState({ stage, progress: frac == null ? null : frac * 0.88, speed, eta, bytes: b });
    };

    const label = plan.streams.length > 1 ? "Downloading video + audio" : "Downloading";
    const firstWait = Math.max(0, ...plan.streams.map((st) => st.available_in || 0));
    if (firstWait > 0.5) onState({ stage: `Waiting ${Math.ceil(firstWait)} s for the site to release the stream`, progress: null, speed: null, eta: null });
    else report(label);
    // Fetch streams in parallel; if one fails, stop the others too.
    const inner = new AbortController();
    const relayAbort = () => inner.abort();
    signal.addEventListener("abort", relayAbort, { once: true });
    let blobs;
    try {
      blobs = await Promise.all(plan.streams.map((s, i) =>
        fetchStream(s, inner.signal, (n) => { got[i] = n; report(label); })));
    } catch (e) {
      inner.abort();
      throw signal.aborted ? new CancelledError() : e;
    } finally {
      signal.removeEventListener("abort", relayAbort);
    }

    let cover = null;
    if (plan.cover) {
      try {
        const raw = await fetchStream({ ...plan.cover, ext: "jpg", chunk: 4 << 20 }, signal, () => {});
        cover = raw.size ? await toJpeg(raw) : null;
      } catch (e) { if (e.cancelled) throw e; cover = null; }
    }

    if (!needsFFmpeg({ ...plan, cover })) {
      onState({ stage: "Saving", progress: 1, speed: null, eta: null });
      return new Blob([blobs[0]], { type: MIME[plan.format] });
    }

    onState({ stage: "Starting converter", progress: 0.88, speed: null, eta: null });
    const host = new FFmpegHost(this.core);
    const abort = () => host.terminate();
    signal.addEventListener("abort", abort, { once: true });
    try {
      await host.start();
      const files = plan.streams.map((s, i) => ({ name: `in${i}.${s.ext || "bin"}`, data: blobs[i] }));
      if (cover) files.push({ name: "cover.jpg", data: cover });
      await host.call("mount", { dir: "/in", blobs: files });
      const inputs = plan.streams.map((s, i) => ({ path: `/in/${files[i].name}`, role: s.role, acodec: s.acodec }));
      const out = `/out.${plan.format}`;
      const stage = plan.kind === "audio" ? `Converting to ${plan.format.toUpperCase()} on this device`
        : "Merging on this device";
      host.onProgress = ({ progress }) => {
        if (progress > 0 && progress <= 1) onState({ stage, progress: 0.88 + progress * 0.11, speed: null, eta: null });
      };
      onState({ stage, progress: 0.88, speed: null, eta: null });
      const code = await host.exec(buildArgs(plan, inputs, cover ? "/in/cover.jpg" : null, out));
      if (code !== 0) {
        const tail = host.logs.slice(-6).join(" ").slice(-300);
        throw new FallbackError(`The in-browser converter failed (${tail || "code " + code}).`);
      }
      const data = await host.call("read", { path: out });
      onState({ stage: "Saving", progress: 1, speed: null, eta: null });
      return new Blob([data], { type: MIME[plan.format] || "application/octet-stream" });
    } catch (e) {
      if (signal.aborted) throw new CancelledError();
      throw e;
    } finally {
      signal.removeEventListener("abort", abort);
      host.terminate();
    }
  }
}

export function saveBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60_000);
}
