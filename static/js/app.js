import { BrowserEngine, browserCapable, prefersBrowser, saveBlob } from "./engine.js";
import { installShapes } from "./shapes.js";
import { CONTRASTS, DEFAULT_SEED, DEFAULT_STYLE, PRESETS, STYLES, applyAppearance, isHex, loadAppearance,
  saveAppearance, swatchColor } from "./theme.js";
import { buildZip } from "./zip.js";

// ── tiny helpers ────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
};
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const fmtBytes = (n) => {
  if (n == null || !isFinite(n)) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
};
const fmtEta = (s) => {
  if (s == null || !isFinite(s) || s < 0) return "";
  s = Math.round(s);
  const m = Math.floor(s / 60), r = s % 60;
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
};
const safeName = (s) => (s || "download").replace(/[\\/:*?"<>|\x00-\x1f]/g, "").replace(/\s+/g, " ").trim().slice(0, 150) || "download";

async function api(path, { method = "GET", body, signal } = {}) {
  const res = await fetch(path, {
    method, signal, credentials: "same-origin",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* not json */ }
  if (res.status === 401 && data.login) { location.reload(); }
  if (!res.ok) {
    const err = new Error(data.error || `Server error (${res.status})`);
    err.status = res.status; err.data = data;
    throw err;
  }
  return data;
}

function alertBox(el, type, text, spinner = false) {
  el.replaceChildren(h("div", { class: `alert alert-${type}${spinner ? " loading" : ""}`, role: type === "error" ? "alert" : null },
    spinner ? h("span", { class: "loader sm", "aria-hidden": "true" }) : null, h("span", { text })));
}

// ── snackbar ────────────────────────────────────────────────────────────
let snackTimer = null;
function toast(text, action) {
  const bar = $("snackbar");
  $("snackbar-text").textContent = text;
  bar.querySelector("button")?.remove();
  if (action) bar.append(h("button", { class: "btn btn-text", type: "button", text: action.label,
    onclick: () => { bar.classList.remove("show"); action.run(); } }));
  bar.classList.add("show");
  clearTimeout(snackTimer);
  snackTimer = setTimeout(() => bar.classList.remove("show"), action ? 6000 : 3500);
}

// ── per-visitor UI preferences ──────────────────────────────────────────
const UI = Object.assign({ autosave: true }, store.get("ytdlui.ui", {}));
const saveUI = () => store.set("ytdlui.ui", UI);
const clear = (el) => el.replaceChildren();

// ── state ───────────────────────────────────────────────────────────────
let CONFIG;
let engine;
const prefs = store.get("ytdlui.prefs", {});
const S = {
  url: "", info: null, noPlaylist: false,
  kind: prefs.kind || "video", format: null, quality: null,
  embed: null, zip: prefs.zip ?? true, processing: "server",
  selected: new Set(),
};
const jobs = new Map();      // id → job view-model
let pollTimer = null;

function savePrefs() {
  Object.assign(prefs, {
    kind: S.kind, zip: S.zip, processing: S.processing,
    [`format_${S.kind}`]: S.format, [`quality_${S.kind}`]: S.quality, [`embed_${S.kind}`]: S.embed,
  });
  store.set("ytdlui.prefs", prefs);
}

// ── processing location ─────────────────────────────────────────────────
function procAvailability() {
  const mode = CONFIG.processing.mode;
  const canBrowser = mode !== "server" && browserCapable();
  const canServer = CONFIG.server_downloads;
  return { canBrowser, canServer, choice: mode === "choice" && canBrowser && canServer };
}

function initialProcessing() {
  const { canBrowser, canServer, choice } = procAvailability();
  if (!canBrowser) return "server";
  if (!canServer || CONFIG.processing.mode === "browser") return "browser";
  if (choice && (prefs.processing === "server" || prefs.processing === "browser")) return prefs.processing;
  const d = CONFIG.processing.default;
  if (d === "auto") return prefersBrowser() ? "browser" : "server";
  return d === "browser" ? "browser" : "server";
}

function renderProcessing() {
  const { canBrowser, canServer, choice } = procAvailability();
  $("proc-row").classList.toggle("hidden", !choice);
  const fixed = $("proc-fixed");
  fixed.classList.add("hidden");
  if (choice) {
    document.querySelectorAll("[data-proc]").forEach((b) => {
      const on = b.dataset.proc === S.processing;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", on);
    });
    const busy = CONFIG.load?.busy;
    $("proc-note").textContent = S.processing === "browser"
      ? "Your device downloads and converts the file — easier on the server. The first time loads a ~31 MB converter."
      : busy ? "The server is busy right now — “This device” may be faster."
        : "The server downloads and converts the file for you.";
  } else if (CONFIG.processing.mode === "browser" && canBrowser) {
    fixed.textContent = "Files are processed on your device — this server only looks up the streams.";
    fixed.classList.remove("hidden");
  } else if (CONFIG.processing.mode === "browser" && !canBrowser && canServer) {
    fixed.textContent = "Your browser can't convert files itself, so the server will do it.";
    fixed.classList.remove("hidden");
  } else if (!canServer && !canBrowser) {
    fixed.textContent = "This browser can't process downloads, and the server only allows in-browser processing.";
    fixed.classList.remove("hidden");
  }
}

// ── fetch info ──────────────────────────────────────────────────────────
function extractUrl(text) {
  const m = (text || "").match(/https?:\/\/[^\s<>"']+/i);
  return m ? m[0] : (text || "").trim();
}

async function fetchInfo(noPlaylist = false) {
  const url = extractUrl($("url-input").value);
  const alert = $("fetch-alert");
  if (!url) { alertBox(alert, "warn", "Paste a link first."); return; }
  $("url-input").value = url;
  S.url = url;
  S.noPlaylist = noPlaylist;
  $("fetch-btn").disabled = true;
  $("info-card").classList.add("hidden");
  $("dl-panel").classList.add("hidden");
  clear($("dl-alert"));
  alertBox(alert, "info", "Looking up the link…", true);
  try {
    const info = await api("api/info", { method: "POST", body: { url, no_playlist: noPlaylist } });
    S.info = info;
    clear(alert);
    renderInfo();
    renderOptions();
    $("info-card").classList.remove("hidden");
    $("dl-panel").classList.remove("hidden");
  } catch (e) {
    alertBox(alert, "error", e.message === "Failed to fetch" ? "Couldn't reach the server. Is it running?" : e.message);
  } finally {
    $("fetch-btn").disabled = false;
  }
}

function renderInfo() {
  const i = S.info;
  const img = $("thumb");
  img.parentElement.classList.toggle("hidden", !i.thumbnail);
  if (i.thumbnail) img.src = i.thumbnail; else img.removeAttribute("src");
  $("info-title").textContent = i.title;
  $("info-detail").textContent = [i.uploader, i.duration_str, i.extractor].filter(Boolean).join("  ·  ");
  const pills = $("info-pills");
  clear(pills);
  if (i.type === "playlist") {
    pills.append(h("span", { class: "pill", text: `${i.count} item${i.count === 1 ? "" : "s"}` }));
    if (i.single_available) {
      pills.append(h("button", { class: "pill", type: "button", style: "cursor:pointer", text: "Just this video →",
        onclick: () => fetchInfo(true) }));
    }
  } else if (S.noPlaylist) {
    pills.append(h("button", { class: "pill", type: "button", style: "cursor:pointer", text: "← Whole playlist",
      onclick: () => fetchInfo(false) }));
  }
  if (i.is_live) pills.append(h("span", { class: "pill pill-warn", text: "live" }));
  if (i.too_long) pills.append(h("span", { class: "pill pill-warn", text: `over the ${i.max_duration_minutes} min limit` }));
}

// ── options ─────────────────────────────────────────────────────────────
function renderOptions() {
  const i = S.info;
  const canVideo = i.has_video !== false;
  if (!canVideo) S.kind = "audio";
  $("tab-video").disabled = !canVideo;
  setKind(S.kind, false);

  const isPl = i.type === "playlist";
  $("playlist-wrap").classList.toggle("hidden", !isPl);
  if (isPl) {
    S.selected = new Set(i.entries.filter((e) => !e.too_long).slice(0, i.max_items).map((e) => e.index));
    renderPlaylist();
  }
  renderProcessing();
  renderZipRow();
  updateDownloadButton();
}

function setKind(kind, save = true) {
  S.kind = kind;
  $("tab-video").classList.toggle("active", kind === "video");
  $("tab-audio").classList.toggle("active", kind === "audio");
  $("tab-video").setAttribute("aria-selected", kind === "video");
  $("tab-audio").setAttribute("aria-selected", kind === "audio");
  const list = CONFIG.formats[kind];
  const pref = prefs[`format_${kind}`];
  S.format = list.some((f) => f.id === pref) ? pref : list[0].id;
  const embedPref = prefs[`embed_${kind}`];
  S.embed = typeof embedPref === "boolean" ? embedPref : kind === "audio";
  $("embed-toggle").checked = S.embed;
  renderFormats();
  if (save) savePrefs();
}

function renderFormats() {
  const row = $("format-chips");
  clear(row);
  for (const f of CONFIG.formats[S.kind]) {
    row.append(h("button", {
      type: "button", class: "chip" + (f.id === S.format ? " selected" : ""), role: "radio",
      "aria-checked": f.id === S.format, title: f.note, text: f.label,
      onclick: () => { S.format = f.id; renderFormats(); savePrefs(); },
    }));
  }
  $("format-note").textContent = CONFIG.formats[S.kind].find((f) => f.id === S.format)?.note || "";
  renderQualities();
  updateEmbedNote();
}

function renderQualities() {
  const wrap = $("quality-wrap");
  const row = $("quality-chips");
  clear(row);
  if (S.kind === "audio") {
    const fmt = CONFIG.formats.audio.find((f) => f.id === S.format);
    if (!fmt.lossy) { wrap.classList.add("hidden"); S.quality = null; return; }
    wrap.classList.remove("hidden");
    $("quality-label").textContent = "Bitrate";
    const pref = Number(String(prefs.quality_audio || "").replace("k", ""));
    const br = CONFIG.bitrates.includes(pref) ? pref : CONFIG.default_bitrate;
    S.quality = `${br}k`;
    for (const b of CONFIG.bitrates) {
      row.append(h("button", {
        type: "button", class: "chip" + (`${b}k` === S.quality ? " selected" : ""), role: "radio",
        "aria-checked": `${b}k` === S.quality, text: `${b}k`,
        onclick: () => { S.quality = `${b}k`; renderQualitySel(); savePrefs(); },
      }));
    }
    return;
  }
  wrap.classList.remove("hidden");
  $("quality-label").textContent = "Quality";
  const qs = [{ label: "best", height: null, size: null }, ...(S.info.video_qualities || [])];
  const pref = prefs.quality_video;
  S.quality = qs.some((q) => q.label === pref) ? pref : (qs[1]?.label || "best");
  for (const q of qs) {
    const label = q.label === "best" ? "Best" : q.label;
    row.append(h("button", {
      type: "button", class: "chip" + (q.label === S.quality ? " selected" : ""), role: "radio",
      "aria-checked": q.label === S.quality, "data-q": q.label,
      onclick: () => { S.quality = q.label; renderQualitySel(); savePrefs(); },
    }, label, q.size ? h("small", { text: `≈ ${fmtBytes(q.size)}` }) : null));
  }
}

function renderQualitySel() {
  document.querySelectorAll("#quality-chips .chip").forEach((b) => {
    const on = (b.dataset.q || b.textContent) === S.quality;
    b.classList.toggle("selected", on);
    b.setAttribute("aria-checked", on);
  });
  updateDownloadButton();
}

function updateEmbedNote() {
  const note = S.kind === "audio"
    ? (["mp3", "m4a", "flac", "opus"].includes(S.format) ? "Title, artist and cover art inside the file" : "Title and artist tags")
    : "Title and channel tags" + (["mp4", "mkv"].includes(S.format) ? " plus thumbnail" : "");
  $("embed-note").textContent = note;
  updateDownloadButton();
}

function renderPlaylist() {
  const list = $("pl-list");
  clear(list);
  const max = S.info.max_items;
  for (const e of S.info.entries) {
    const disabled = e.too_long;
    const cb = h("input", { type: "checkbox", checked: S.selected.has(e.index), disabled,
      "aria-label": e.title });
    cb.addEventListener("change", () => {
      if (cb.checked) {
        if (S.selected.size >= max) { cb.checked = false; alertBox($("dl-alert"), "warn", `This server allows at most ${max} items per download.`); return; }
        S.selected.add(e.index);
      } else S.selected.delete(e.index);
      updatePlaylistCount();
    });
    list.append(h("label", { class: "pl-item" + (disabled ? " disabled" : ""), title: disabled ? "Longer than this server allows" : e.title },
      cb, h("span", { class: "pl-idx", text: e.index }), h("span", { class: "pl-title", text: e.title }),
      h("span", { class: "pl-dur", text: e.duration_str || "" })));
  }
  updatePlaylistCount();
}

function updatePlaylistCount() {
  const n = S.info.entries.length;
  $("pl-count").textContent = `${S.selected.size} of ${n} selected`;
  updateDownloadButton();
}

function renderZipRow() {
  const isPl = S.info?.type === "playlist";
  $("zip-row").classList.toggle("hidden", !(isPl && S.processing === "browser"));
  $("zip-toggle").checked = S.zip;
  updateOptList();
}

function updateOptList() {
  const rows = [...document.querySelectorAll("#opt-list > .opt-row")].filter((r) => !r.classList.contains("hidden"));
  document.querySelectorAll("#opt-list > .opt-row").forEach((r) => r.classList.remove("first-visible", "last-visible"));
  rows[0]?.classList.add("first-visible");
  rows[rows.length - 1]?.classList.add("last-visible");
}

function updateDownloadButton() {
  const btn = $("dl-btn");
  if (!S.info) return;
  const fmt = (S.format || "").toUpperCase();
  if (S.info.type === "playlist") {
    const n = S.selected.size;
    btn.replaceChildren(h("span", { class: "msr", text: n ? "playlist_play" : "checklist" }),
      h("span", { text: n ? `Download ${n} item${n === 1 ? "" : "s"} · ${fmt}` : "Select items to download" }));
    btn.disabled = n === 0;
  } else {
    const q = S.quality ? (S.quality === "best" ? "best quality" : S.quality) : "";
    btn.replaceChildren(h("span", { class: "msr", text: "download" }), h("span", { text: `Download ${fmt}${q ? " · " + q : ""}` }));
    btn.disabled = !!S.info.too_long;
  }
}

// ── jobs: shared view ───────────────────────────────────────────────────
function persistServerJobs() {
  const list = [...jobs.values()].filter((j) => j.mode === "server" && j.status !== "removed")
    .map(({ id, title, thumb, kind, format, quality, playlist, saved, created, note }) =>
      ({ id, title, thumb, kind, format, quality, playlist, saved, created, note }));
  store.set("ytdlui.jobs", list.slice(0, 30));
}

function addJob(j) {
  j.created ??= Date.now();
  j.status ??= "queued";
  j.progress ??= 0;
  j.files ??= [];
  jobs.set(j.id, j);
  buildJobCard(j);
  renderJob(j);
  updateEmpty();
  if (j.mode === "server") persistServerJobs();
  return j;
}

function buildJobCard(j) {
  j.el = {
    root: h("article", { class: "job" }),
    title: h("div", { class: "job-title" }),
    tags: h("div", { class: "job-tags" }),
    bar: h("div", { class: "progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" },
      h("div", { class: "ind" }), h("div", { class: "trk" })),
    st: h("span", { class: "st" }),
    actions: h("div", { class: "job-actions" }),
    files: h("div", { class: "job-files hidden" }),
    note: h("div", { class: "job-note hidden" }),
  };
  const x = h("button", { class: "job-x sl", type: "button", "aria-label": "Remove", title: "Cancel / remove",
    onclick: () => removeJob(j) }, h("span", { class: "msr", text: "close" }));
  const thumb = h("div", { class: "job-thumb" }, j.thumb ? h("img", { src: j.thumb, alt: "", referrerpolicy: "no-referrer", loading: "lazy" }) : null);
  j.el.root.append(thumb, h("div", { class: "job-body" },
    h("div", { class: "job-top" }, j.el.title, x), j.el.tags, j.el.bar,
    h("div", { class: "job-status" }, j.el.st, j.el.actions), j.el.note, j.el.files));
  $("jobs").prepend(j.el.root);
}

function renderJob(j) {
  const e = j.el;
  if (!e) return;
  e.root.className = `job ${j.status}`;
  e.title.textContent = j.title || "Download";
  e.title.title = j.title || "";
  e.tags.replaceChildren(...[
    h("span", { class: "tag", text: (j.format || "").toUpperCase() }),
    j.quality ? h("span", { class: "tag", text: j.quality }) : null,
    j.playlist ? h("span", { class: "tag", text: j.item_count ? `${j.item_count} items` : "playlist" }) : null,
    h("span", { class: "tag" + (j.mode === "browser" ? " tag-device" : ""), text: j.mode === "browser" ? "this device" : "server" }),
  ].filter(Boolean));
  const active = j.status === "queued" || j.status === "running";
  const p = j.progress;
  const indet = active && (p == null || j.status === "queued");
  const shown = j.status === "done" ? 1 : (p || 0);
  e.bar.classList.toggle("indeterminate", indet);
  e.bar.classList.toggle("complete", j.status === "done");
  // M3 Expressive: the wave flattens at the very start, when finished and when stopped
  e.bar.classList.toggle("flat", !indet && (j.status !== "running" || shown < 0.02 || shown > 0.985));
  e.bar.style.setProperty("--p", String(Math.min(1, Math.max(0, shown))));
  if (indet) e.bar.removeAttribute("aria-valuenow"); else e.bar.setAttribute("aria-valuenow", String(Math.round(shown * 100)));

  let text = "";
  if (j.status === "queued") text = j.queue_position ? `Waiting in queue (#${j.queue_position})` : (j.stage || "Starting…");
  else if (j.status === "running") {
    const bits = [j.stage || "Working"];
    if (p != null) bits.push(`${Math.round(p * 100)}%`);
    if (j.speed) bits.push(`${fmtBytes(j.speed)}/s`);
    if (j.eta) bits.push(`${fmtEta(j.eta)} left`);
    text = bits.join(" · ");
  } else if (j.status === "done") {
    const size = j.files.reduce((n, f) => n + (f.size || 0), 0);
    text = (j.stage && j.stage !== "Ready" ? j.stage : "Done") + (size ? ` · ${fmtBytes(size)}` : "");
  } else if (j.status === "error") text = j.error || "Failed";
  else if (j.status === "cancelled") text = "Cancelled";
  else if (j.status === "expired") text = "Expired — the server deleted this file";
  e.st.textContent = text;
  e.st.title = text;

  e.actions.replaceChildren();
  if (active) {
    e.actions.append(h("button", { class: "btn btn-danger btn-small sl", type: "button", onclick: () => cancelJob(j) },
      h("span", { class: "msr", text: "cancel" }), "Cancel"));
  } else if (j.status === "done") {
    if (j.mode === "server" || j.blob) {
      e.actions.append(h("button", { class: `btn ${j.saved ? "btn-tonal" : "btn-filled"} btn-small sl`, type: "button", onclick: () => saveJob(j) },
        h("span", { class: "msr", text: j.saved ? "download_done" : "download" }), j.saved ? "Save again" : "Save"));
    }
  } else if (j.status === "error" || j.status === "cancelled") {
    if (j.retry) e.actions.append(h("button", { class: "btn btn-tonal btn-small sl", type: "button", onclick: () => { removeJob(j); j.retry(); } },
      h("span", { class: "msr", text: "restart_alt" }), "Retry"));
  }

  e.note.classList.toggle("hidden", !j.note);
  e.note.textContent = j.note || "";

  const showFiles = j.status === "done" && j.mode === "server" && j.files.length > 1;
  e.files.classList.toggle("hidden", !showFiles);
  updateEmpty();
  if (showFiles && e.files.childElementCount !== j.files.length) {
    e.files.replaceChildren(...j.files.map((f) => h("a", { href: `api/jobs/${j.id}/files/${f.index}`, download: f.name },
      h("span", { text: f.name }), h("span", { class: "mono", text: fmtBytes(f.size) }))));
  }
}

function removeJob(j) {
  if (j.status === "queued" || j.status === "running") cancelJob(j);
  if (j.mode === "server") api(`api/jobs/${j.id}`, { method: "DELETE" }).catch(() => {});
  j.el?.root.remove();
  j.blob = null;
  jobs.delete(j.id);
  persistServerJobs();
  updateEmpty();
}

function updateEmpty() {
  $("jobs-empty").classList.toggle("hidden", jobs.size > 0);
  $("clear-finished").classList.toggle("hidden", ![...jobs.values()].some((j) => !["queued", "running"].includes(j.status)));
}

function cancelJob(j) {
  if (j.mode === "browser") { j.abort?.(); return; }
  api(`api/jobs/${j.id}`, { method: "DELETE" }).catch(() => {});
  j.status = "cancelled";
  renderJob(j);
}

function triggerUrlDownload(url) {
  const a = h("a", { href: url, download: "" });
  a.style.display = "none";
  document.body.append(a);
  a.click();
  a.remove();
}

function saveJob(j) {
  if (j.mode === "browser" && j.blob) saveBlob(j.blob, j.filename);
  else if (j.mode === "server") triggerUrlDownload(`api/jobs/${j.id}/file`);
  j.saved = true;
  renderJob(j);
  persistServerJobs();
}

// ── server jobs ─────────────────────────────────────────────────────────
async function startServerJob(req, view, note) {
  const data = await api("api/jobs", { method: "POST", body: req });
  const j = addJob({ ...view, mode: "server", id: data.id, note });
  applyServerState(j, data);
  j.retry = () => startServerJob(req, view).catch((e) => alertBox($("dl-alert"), "error", e.message));
  schedulePoll();
  return j;
}

function applyServerState(j, d) {
  const wasDone = j.status === "done";
  Object.assign(j, {
    status: d.status, progress: d.progress, stage: d.stage, speed: d.speed, eta: d.eta, error: d.error,
    files: d.files || [], queue_position: d.queue_position, item_count: d.item_count || j.item_count,
  });
  if (!j.title && d.title) j.title = d.title;
  if (d.status === "done" && !wasDone && !j.saved) {
    if (UI.autosave) { saveJob(j); toast(`Saving “${j.title || "download"}”`); }
    else toast(`“${j.title || "Download"}” is ready`, { label: "Save", run: () => saveJob(j) });
  }
  if (d.skipped && j.playlist && d.status === "done") j.note = `${d.skipped} item(s) couldn't be downloaded and were skipped.`;
  renderJob(j);
}

function schedulePoll() {
  if (pollTimer) return;
  pollTimer = setTimeout(poll, 900);
}

async function poll() {
  pollTimer = null;
  const active = [...jobs.values()].filter((j) => j.mode === "server" && (j.status === "queued" || j.status === "running"));
  const pending = [...jobs.values()].filter((j) => j.mode === "server" && j.status === "done");
  const ids = [...active, ...pending].map((j) => j.id);
  if (!ids.length) return;
  try {
    const data = await api(`api/jobs?ids=${ids.join(",")}`);
    if (data.load) { CONFIG.load = { ...CONFIG.load, ...data.load }; }
    const seen = new Set(data.jobs.map((d) => d.id));
    for (const d of data.jobs) { const j = jobs.get(d.id); if (j) applyServerState(j, d); }
    for (const j of [...active, ...pending]) {
      if (!seen.has(j.id)) { j.status = j.status === "done" ? "expired" : "error"; if (j.status === "error") j.error = "The server lost track of this download (it may have restarted)."; renderJob(j); }
    }
    persistServerJobs();
  } catch { /* transient — try again */ }
  if ([...jobs.values()].some((j) => j.mode === "server" && (j.status === "queued" || j.status === "running"))) {
    schedulePoll();
  } else if ([...jobs.values()].some((j) => j.mode === "server" && j.status === "done")) {
    pollTimer = setTimeout(poll, 30_000);   // notice expiry eventually
  }
}

async function restoreServerJobs() {
  const saved = store.get("ytdlui.jobs", []);
  if (!saved.length) return;
  let data;
  try { data = await api(`api/jobs?ids=${saved.map((j) => j.id).join(",")}`); } catch { return; }
  const live = new Map(data.jobs.map((d) => [d.id, d]));
  for (const s of saved.reverse()) {
    const d = live.get(s.id);
    if (!d) continue;
    const j = addJob({ ...s, mode: "server", saved: s.saved ?? true });
    applyServerState(j, d);
  }
  persistServerJobs();
  schedulePoll();
}

// ── browser jobs ────────────────────────────────────────────────────────
let browserSeq = 0;

function startBrowserJob(req, view, info) {
  const ac = new AbortController();
  const j = addJob({ ...view, mode: "browser", id: `b${++browserSeq}`, status: "queued", stage: "Waiting for the converter", progress: null });
  j.abort = () => ac.abort();
  j.retry = () => startBrowserJob(req, view, info);
  const set = (patch) => { Object.assign(j, patch); renderJob(j); };

  engine.enqueue(async () => {
    if (ac.signal.aborted) throw Object.assign(new Error("Cancelled"), { cancelled: true });
    set({ status: "running", stage: "Looking up streams", progress: null });
    if (!req.playlist) {
      const plan = await engine.resolve(req, ac.signal);
      const blob = await engine.process(plan, { signal: ac.signal, onState: set });
      j.blob = blob;
      j.filename = plan.filename;
      if (UI.autosave) { saveBlob(blob, plan.filename); toast(`Saved “${plan.filename}”`); }
      else toast(`“${plan.filename}” is ready`, { label: "Save", run: () => saveJob(j) });
      set({ status: "done", progress: 1, stage: "Done", files: [{ name: plan.filename, size: blob.size }], saved: UI.autosave, speed: null, eta: null });
      return;
    }
    const entries = info.entries.filter((e) => req.items.includes(e.index));
    if (entries.some((e) => !e.url)) throw Object.assign(new Error("This playlist can't be processed in the browser."), { fallback: true });
    const outputs = [];
    let skipped = 0;
    j.item_count = entries.length;
    for (let k = 0; k < entries.length; k++) {
      const e = entries[k];
      const scale = (p) => (k + (p ?? 0)) / entries.length;
      set({ stage: `Item ${k + 1} of ${entries.length} · looking up`, progress: scale(0) });
      try {
        const plan = await engine.resolve({ ...req, url: e.url, playlist: false, items: [] }, ac.signal);
        const blob = await engine.process(plan, {
          signal: ac.signal,
          onState: (s) => set({ ...s, stage: `Item ${k + 1} of ${entries.length} · ${s.stage}`, progress: scale(s.progress) }),
        });
        const name = `${String(e.index).padStart(3, "0")} - ${plan.filename}`;
        if (req.zip) outputs.push({ name, blob });
        else saveBlob(blob, name);
        j.files.push({ name, size: blob.size });
      } catch (err) {
        if (ac.signal.aborted || err.cancelled) throw err;
        skipped++;
        set({ note: `${skipped} item(s) skipped — last error: ${err.message}` });
      }
    }
    if (!j.files.length) throw new Error("None of the items could be downloaded.");
    if (outputs.length) {
      set({ stage: "Building ZIP", progress: 0.99, speed: null, eta: null });
      const zip = await buildZip(outputs);
      j.blob = zip;
      j.filename = `${safeName(info.title)}.zip`;
      saveBlob(zip, j.filename);
    }
    set({ status: "done", progress: 1, stage: skipped ? `Done — ${skipped} skipped` : "Done", saved: true, speed: null, eta: null });
  }).catch(async (err) => {
    if (ac.signal.aborted || err.cancelled) { set({ status: "cancelled" }); return; }
    const { canServer } = procAvailability();
    if (err.fallback && canServer && CONFIG.processing.fallback) {
      removeJob(j);
      try {
        await startServerJob(req, view, `Processed on the server instead: ${err.message}`);
      } catch (e2) {
        alertBox($("dl-alert"), "error", e2.message);
      }
      return;
    }
    set({ status: "error", error: err.message, speed: null, eta: null });
  });
  return j;
}

window.addEventListener("beforeunload", (e) => {
  if ([...jobs.values()].some((j) => j.mode === "browser" && (j.status === "running" || j.status === "queued"))) {
    e.preventDefault();
    e.returnValue = "";
  }
});

// ── download button ─────────────────────────────────────────────────────
async function startDownload() {
  if (!S.info) return;
  const i = S.info;
  const isPl = i.type === "playlist";
  const req = {
    url: S.url, kind: S.kind, format: S.format, quality: S.quality || "", embed: S.embed,
    playlist: isPl, items: isPl ? [...S.selected].sort((a, b) => a - b) : [], title: i.title,
    zip: S.zip,
  };
  if (!isPl && S.noPlaylist) req.playlist = false;
  const view = {
    title: i.title, thumb: i.thumbnail, kind: S.kind, format: S.format, playlist: isPl,
    quality: S.kind === "audio" ? (S.quality || "lossless") : (S.quality === "best" ? "best" : S.quality),
    item_count: isPl ? req.items.length : 0,
  };
  clear($("dl-alert"));
  const btn = $("dl-btn");
  btn.disabled = true;
  try {
    if (S.processing === "browser") startBrowserJob(req, view, i);
    else await startServerJob(req, view);
    $("jobs-title").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    alertBox($("dl-alert"), "error", e.message);
  } finally {
    setTimeout(() => { btn.disabled = false; updateDownloadButton(); }, 600);
  }
}

// ── admin ───────────────────────────────────────────────────────────────
function showSettingsPage(page, dir = "fwd") {
  const main = $("settings-main"), admin = $("admin-page");
  const show = page === "admin" ? admin : main, hide = page === "admin" ? main : admin;
  if (!show.classList.contains("hidden")) return;
  hide.classList.add("hidden");
  show.classList.remove("hidden", "enter-fwd", "enter-back");
  void show.offsetWidth;
  show.classList.add(dir === "back" ? "enter-back" : "enter-fwd");
}

function openSettings(page = "main") {
  const dlg = $("settings-modal");
  renderAppearance();
  renderDownloadPrefs();
  if (!dlg.open) {
    $("settings-main").classList.toggle("hidden", page === "admin");
    $("admin-page").classList.toggle("hidden", page !== "admin");
    dlg.showModal();
  } else {
    showSettingsPage(page);
  }
}

async function openAdmin() {
  openSettings("admin");
  const setup = CONFIG.auth.admin_setup;
  if (!CONFIG.auth.admin && !(setup && setup !== "done")) {
    $("admin-login").classList.remove("hidden");
    $("admin-panel").classList.add("hidden");
    setTimeout(() => $("admin-pass").focus(), 50);
    return;
  }
  $("admin-login").classList.add("hidden");
  const panel = $("admin-panel");
  panel.classList.remove("hidden");
  if (setup === "totp" || setup === "password") { renderSetup(setup); return; }
  if (!panel.childElementCount) panel.append(h("p", {}, h("span", { class: "loader sm" }), "  Loading…"));
  try {
    const [st, jl] = await Promise.all([api("api/admin/status"), api("api/admin/jobs")]);
    renderAdmin(st, jl.jobs);
  } catch (e) {
    panel.replaceChildren(h("div", { class: "alert alert-error", text: e.message }));
  }
}

function adminAction(label, path, { confirmText, body, cls = "btn-secondary", after } = {}) {
  return h("button", {
    class: `btn btn-small ${cls}`, type: "button", text: label,
    onclick: async (ev) => {
      if (confirmText && !confirm(confirmText)) return;
      const b = ev.currentTarget;
      b.disabled = true;
      const old = b.textContent;
      b.textContent = "Working…";
      const msg = $("admin-msg");
      try {
        let r;
        try { r = await api(path, { method: "POST", body: body || {} }); }
        catch (e) {
          if (e.status === 409 && confirm(`${e.message}\n\nForce it anyway?`)) r = await api(path, { method: "POST", body: { ...(body || {}), force: true } });
          else throw e;
        }
        alertBox(msg, r.ok === false ? "error" : "success", r.message || (r.removed != null ? `Removed ${r.removed} finished download(s).` : "Done."));
        if (after) after(r);
        else setTimeout(openAdmin, 400);
      } catch (e) {
        alertBox(msg, "error", e.message);
      } finally {
        b.disabled = false;
        b.textContent = old;
      }
    },
  });
}

// ── admin security: 2FA setup, password change ─────────────────────────
const field = (attrs) => h("div", { class: "field" }, h("input", { required: true, ...attrs }));
const codeInput = (id = "sec-code", required = true) => field({ id, type: "text", inputmode: "numeric",
  autocomplete: "one-time-code", placeholder: "6-digit code from your authenticator", maxlength: "20",
  "aria-label": "Authenticator code", required: required || null });

async function refreshConfig() { CONFIG = await api("api/config"); updateAdminDot(); }

function recoveryBlock(codes, onDone) {
  const text = "YTDL-UI admin recovery codes — each works once instead of a 2FA code.\n\n" + codes.join("\n") + "\n";
  return h("div", {},
    h("p", { text: "Save these recovery codes somewhere safe. Each one can be used once if you lose your phone. They won't be shown again." }),
    h("pre", { class: "mono", style: "background:var(--bg-alt);border:1px solid var(--border);border-radius:6px;padding:12px;columns:2;font-size:0.85rem;margin-bottom:12px", text: codes.join("\n") }),
    h("div", { class: "btn-row" },
      h("button", { class: "btn btn-small btn-secondary", type: "button", text: "Download as .txt",
        onclick: () => saveBlob(new Blob([text], { type: "text/plain" }), "ytdl-ui-recovery-codes.txt") }),
      h("button", { class: "btn btn-small btn-secondary", type: "button", text: "Copy",
        onclick: () => navigator.clipboard?.writeText(text) }),
      onDone ? h("button", { class: "btn btn-small btn-primary", type: "button", text: "I've saved them — continue", onclick: onDone }) : null));
}

function passwordChangeForm({ intro, submitLabel = "Change password", onDone }) {
  const msg = h("div");
  const form = h("form", { autocomplete: "off" },
    intro ? h("p", { text: intro }) : null,
    field({ id: "pw-current", type: "password", autocomplete: "current-password", placeholder: "Current password", "aria-label": "Current password" }),
    field({ id: "pw-new", type: "password", autocomplete: "new-password", placeholder: "New password (10+ characters)", minlength: "10", "aria-label": "New password" }),
    field({ id: "pw-confirm", type: "password", autocomplete: "new-password", placeholder: "Repeat new password", "aria-label": "Repeat new password" }),
    codeInput("pw-code"),
    h("button", { class: "btn btn-primary btn-full", type: "submit", text: submitLabel }), msg);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const v = (id) => form.querySelector("#" + id).value;
    if (v("pw-new") !== v("pw-confirm")) { alertBox(msg, "error", "The new passwords don't match."); return; }
    try {
      const r = await api("api/admin/password", { method: "POST", body: { current: v("pw-current"), new: v("pw-new"), code: v("pw-code") } });
      form.reset();
      alertBox(msg, "success", r.message);
      onDone?.();
    } catch (err) { alertBox(msg, "error", err.message); }
  });
  return form;
}

async function renderSetup(step) {
  const panel = $("admin-panel");
  const steps = h("p", { class: "mono", style: "font-size:0.75rem", text: `Admin setup · step ${step === "totp" ? 1 : 2} of 2` });
  if (step === "password") {
    panel.replaceChildren(steps, h("h3", { style: "margin-bottom:8px", text: "Choose a new admin password" }),
      passwordChangeForm({
        intro: "Two-factor authentication is on. Now replace the initial password — enter it once more, pick a new one, and confirm with a fresh code from your app.",
        submitLabel: "Save new password",
        onDone: async () => { await refreshConfig(); setTimeout(openAdmin, 700); },
      }));
    return;
  }
  panel.replaceChildren(steps, h("p", {}, h("span", { class: "loader sm" }), "  Preparing…"));
  let t;
  try { t = await api("api/admin/totp/begin", { method: "POST", body: {} }); }
  catch (e) { panel.replaceChildren(h("div", { class: "alert alert-error", text: e.message })); return; }
  const msg = h("div");
  const form = h("form", {}, codeInput("totp-code"),
    h("button", { class: "btn btn-primary btn-full", type: "submit", text: "Turn on 2FA" }), msg);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("api/admin/totp/confirm", { method: "POST", body: { code: form.querySelector("#totp-code").value } });
      panel.replaceChildren(steps, h("h3", { style: "margin-bottom:8px", text: "2FA is on" }),
        recoveryBlock(r.recovery_codes, async () => { await refreshConfig(); openAdmin(); }));
    } catch (err) { alertBox(msg, "error", err.message); }
  });
  panel.replaceChildren(steps,
    h("h3", { style: "margin-bottom:8px", text: "Set up two-factor authentication" }),
    h("p", { text: "Admin logins need a code from an authenticator app (Google Authenticator, Microsoft Authenticator, Aegis, 2FAS, 1Password, Bitwarden…). Scan the QR code, then enter the 6-digit code it shows." }),
    h("div", { style: "display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-bottom:14px" },
      t.qr ? h("img", { src: t.qr, alt: "QR code for your authenticator app", width: "180", height: "180", style: "border-radius:8px;background:#fff" }) : null,
      h("div", { style: "flex:1;min-width:200px" },
        h("div", { class: "modal-section-title", text: "Can't scan? Enter this key:" }),
        h("div", { class: "mono", style: "word-break:break-all;font-size:0.9rem;user-select:all", text: t.secret.match(/.{1,4}/g).join(" ") }),
        h("p", { style: "margin-top:6px;font-size:0.75rem", text: "Time-based (TOTP), 6 digits, 30 seconds." }))),
    form);
  setTimeout(() => form.querySelector("#totp-code")?.focus(), 50);
}

function securitySection(sec) {
  const lock = sec.lockouts.length
    ? h("div", { class: "admin-jobs" }, sec.lockouts.map((l) => h("div", {},
        h("span", { text: l.scope }), h("span", { class: "t", text: l.ip }),
        h("span", { text: `${Math.ceil(l.seconds / 60)} min` }),
        adminAction("Unlock", "api/admin/unlock", { body: { scope: l.scope, ip: l.ip } }))))
    : h("p", { style: "margin:0", text: "No one is locked out." });
  const regen = h("form", {},
    field({ id: "rc-current", type: "password", autocomplete: "current-password", placeholder: "Current password", "aria-label": "Current password" }),
    codeInput("rc-code"),
    h("button", { class: "btn btn-small btn-secondary", type: "submit", text: "Generate new recovery codes" }));
  regen.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("api/admin/recovery-codes", { method: "POST",
        body: { current: regen.querySelector("#rc-current").value, code: regen.querySelector("#rc-code").value } });
      regen.replaceWith(recoveryBlock(r.recovery_codes));
    } catch (err) { alertBox($("admin-msg"), "error", err.message); }
  });
  return h("div", { class: "modal-section" },
    h("div", { class: "modal-section-title", text: "Security" }),
    h("p", { style: "font-size:0.8rem", text: `Two-factor authentication: ${sec.totp ? "on" : "off"} · ${sec.recovery_left} recovery code${sec.recovery_left === 1 ? "" : "s"} left · lockout after ${sec.max_attempts} failed attempts (${sec.lockout_minutes} min, doubling on repeats)` }),
    h("details", { style: "margin-bottom:10px" }, h("summary", { text: "Change admin password", style: "cursor:pointer;font-size:0.85rem;margin-bottom:8px" }),
      passwordChangeForm({ intro: "Needs your current password and a code from your authenticator app. Other admin sessions will be signed out." })),
    h("details", { style: "margin-bottom:10px" }, h("summary", { text: "Recovery codes", style: "cursor:pointer;font-size:0.85rem;margin-bottom:8px" }), regen),
    h("div", { class: "modal-section-title", style: "margin-top:6px", text: "Locked-out IPs" }), lock);
}

// ── admin: server settings editor ──────────────────────────────────────
const SETTING_LABELS = {
  processing_mode: "Where downloads are processed", processing_default: "Default for visitors (choice mode)",
  fallback_to_server: "Fall back to the server", relay: "Relay streams for blocked sites",
  browser_max_mb: "Largest in-browser download (MB)", bandwidth_limit: "Bandwidth cap (MB/s, 0 = off)",
  max_concurrent_jobs: "Simultaneous server downloads", max_jobs_per_client: "Downloads per visitor",
  max_queue: "Queue length", max_duration_minutes: "Max video length (min, 0 = off)",
  max_filesize_mb: "Max file size (MB, 0 = off)", max_playlist_items: "Max playlist items",
  job_ttl_minutes: "Keep finished files (min)", job_timeout_minutes: "Download timeout (min)",
  min_free_space_mb: "Keep free disk space (MB)", rate_limit: "Lookups per minute per IP",
  download_rate_limit: "Downloads per minute per IP", allowed_sites: "Allowed sites (comma-separated, empty = all)",
  login_max_attempts: "Failed logins before lockout", login_lockout_minutes: "Lockout length (min)",
  password: "Site password (visitors)", allow_private_urls: "Allow private/LAN URLs",
  allow_web_update: "Allow updates from this panel", ytdlp_auto_update: "Auto-update yt-dlp",
  ytdlp_channel: "yt-dlp channel", ffmpeg_auto_install: "Auto-install ffmpeg", update_check: "Check for YTDL-UI updates",
  privacy_contact: "Privacy contact (email or link)",
};
const SECTION_TITLES = { server: "Server", processing: "Processing", limits: "Limits", security: "Access & security", updates: "Updates" };
const CHOICE_LABELS = {
  server: "Server", browser: "Visitor's device", choice: "Let visitors choose", auto: "Auto (device if capable)",
  stable: "Stable", nightly: "Nightly",
};

function settingInput(st) {
  const common = { "data-setting": st.name, disabled: st.locked || null, "aria-label": SETTING_LABELS[st.name] || st.name };
  if (st.choices) {
    const choices = st.name === "processing_default" ? st.choices : st.choices;
    return h("select", { ...common, class: "set-input" }, choices.map((c) =>
      h("option", { value: c, selected: c === st.value || null, text: CHOICE_LABELS[c] || c })));
  }
  if (st.type === "bool") {
    const cb = h("input", { ...common, type: "checkbox" });
    cb.checked = !!st.value;
    return h("label", { class: "switch" }, cb, h("span"));
  }
  if (st.type === "int" || st.type === "float") {
    return h("input", { ...common, class: "set-input", type: "number", min: "0", step: st.type === "float" ? "0.5" : "1", value: String(st.value) });
  }
  if (st.name === "password") {
    return h("input", { ...common, class: "set-input", type: "password", autocomplete: "new-password",
      placeholder: st.is_set ? "•••••• (set — type to change)" : "not set — anyone can use the site" });
  }
  return h("input", { ...common, class: "set-input", type: "text", value: Array.isArray(st.value) ? st.value.join(", ") : String(st.value ?? "") });
}

function readSetting(el, st) {
  if (el.type === "checkbox") return el.checked;
  if (el.type === "number") return el.value === "" ? st.value : Number(el.value);
  return el.value;
}

function sameValue(a, b) {
  if (Array.isArray(b)) return String(a).split(",").map((x) => x.trim()).filter(Boolean).join(",") === b.join(",");
  return String(a) === String(b);
}

function settingsSection() {
  const wrap = h("div", { class: "modal-section" }, h("div", { class: "modal-section-title", text: "Settings" }),
    h("p", { style: "font-size:0.8rem", text: "Loading…" }));
  api("api/admin/settings").then(({ settings }) => renderSettings(wrap, settings))
    .catch((e) => wrap.replaceChildren(h("div", { class: "alert alert-error", text: e.message })));
  return wrap;
}

function renderSettings(wrap, list) {
  const msg = h("div");
  const byName = Object.fromEntries(list.map((s) => [s.name, s]));
  const groups = {};
  for (const st of list) (groups[st.section] ||= []).push(st);
  const codeRow = h("div", { class: "hidden" }, codeInput("set-code", false),
    h("p", { style: "font-size:0.75rem;margin:-2px 0 8px", text: "Changing a security setting needs a current 2FA code." }));
  const form = h("form", { autocomplete: "off" },
    ...Object.entries(groups).map(([sec, items]) => h("details", { open: sec === "processing" || null, style: "margin-bottom:8px" },
      h("summary", { style: "cursor:pointer;font-size:0.85rem;margin-bottom:6px", text: SECTION_TITLES[sec] || sec }),
      ...items.map((st) => h("div", { class: "opt-row", style: "padding:9px 0" },
        h("div", { class: "opt-text", style: "min-width:0" },
          h("span", { text: SETTING_LABELS[st.name] || st.name, title: st.help }),
          h("small", { text: st.help.length > 150 ? st.help.slice(0, 147) + "…" : st.help, title: st.help }),
          h("small", { style: "color:var(--text-muted)", text: [st.locked ? "🔒 set on the server (env/CLI) — can't change here" : null,
            st.source === "admin" ? "changed in this panel" : st.source === "config" ? "from config.ini" : null,
            st.restart ? "restart to apply" : null, st.sensitive ? "needs 2FA code" : null].filter(Boolean).join(" · ") })),
        h("div", { style: "flex-shrink:0;max-width:52%" }, settingInput(st)))))),
    codeRow,
    h("div", { class: "btn-row" },
      h("button", { class: "btn btn-small btn-primary", type: "submit", text: "Save settings" }),
      h("button", { class: "btn btn-small btn-secondary", type: "button", text: "Reset changed-in-panel to config", onclick: async () => {
        const names = list.filter((s) => s.source === "admin" && !s.locked).map((s) => s.name);
        if (!names.length) { alertBox(msg, "info", "Nothing was changed in this panel."); return; }
        if (!confirm(`Reset ${names.length} setting(s) to config.ini / defaults?`)) return;
        await save(Object.fromEntries(names.map((n) => [n, null])));
      } })),
    msg);
  const changed = () => {
    const out = {};
    for (const el of form.querySelectorAll("[data-setting]")) {
      const st = byName[el.dataset.setting];
      if (st.locked) continue;
      const v = readSetting(el, st);
      if (st.name === "password") { if (v !== "") out[st.name] = v; continue; }
      if (!sameValue(v, st.value)) out[st.name] = v;
    }
    return out;
  };
  form.addEventListener("input", () => {
    codeRow.classList.toggle("hidden", !Object.keys(changed()).some((n) => byName[n].sensitive));
  });
  async function save(changes) {
    try {
      const r = await api("api/admin/settings", { method: "POST", body: { changes, code: form.querySelector("#set-code")?.value || "" } });
      await refreshConfig();
      renderSettings(wrap, r.settings);
      alertBox(wrap.lastElementChild.lastElementChild, r.restart?.length ? "warn" : "success", r.message);
      renderProcessing();
      renderZipRow();
    } catch (err) { alertBox(msg, "error", err.message); }
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const c = changed();
    if (!Object.keys(c).length) { alertBox(msg, "info", "No changes."); return; }
    save(c);
  });
  wrap.replaceChildren(h("div", { class: "modal-section-title", text: "Settings" }), form);
}

function renderAdmin(st, jobList) {
  const panel = $("admin-panel");
  const app = st.app, yt = st.ytdlp;
  const ago = (t) => t ? new Date(t * 1000).toLocaleString() : "never";
  const row = (k, v, sub, action) => [h("div", { class: "k", text: k }), h("div", { class: "v" }, v, sub ? h("small", { text: sub }) : null), h("div", {}, action || "")];

  const appLine = app.update_available ? `${app.current} → ${app.latest} available` : `${app.current}${app.latest ? " (latest)" : ""}`;
  const appAction = app.update_available
    ? (app.can_apply ? adminAction("Update", "api/admin/update-app", { cls: "btn-primary",
        confirmText: `Install YTDL-UI ${app.latest} and restart the server?`,
        after: (r) => { if (r.ok && r.restart) { alertBox($("admin-msg"), "info", "Restarting… the page will reload.", true); waitForRestart(); } } })
      : h("a", { class: "btn btn-small btn-secondary", href: app.url || "#", target: "_blank", rel: "noopener", text: "Release notes" }))
    : null;
  const ytLine = `${yt.current || "not installed"}${yt.update_available ? ` → ${yt.latest}` : ""}`;
  const ff = st.ffmpeg;

  panel.replaceChildren(
    h("div", { id: "admin-msg" }),
    h("div", { class: "modal-section" },
      h("div", { class: "modal-section-title", text: "Versions" }),
      h("div", { class: "kv" },
        row("YTDL-UI", appLine, `${app.method} install · checked ${ago(app.checked_at)}${app.error ? " · " + app.error : ""}`, appAction),
        row("yt-dlp", ytLine, `${yt.kind} · updates via ${yt.method}${yt.auto ? " (automatic)" : ""}${yt.last_result ? " · " + yt.last_result : ""}`,
          adminAction(yt.update_available ? "Update" : "Update now", "api/admin/update-ytdlp", { cls: yt.update_available ? "btn-primary" : "btn-secondary", body: { force: !yt.update_available } })),
        row("ffmpeg", ff.version || (ff.install?.state === "running" ? "installing…" : "not found"), ff.path || (ff.install?.error || ""),
          !ff.path && ff.installable ? adminAction("Install", "api/admin/install-ffmpeg") : null),
        row("JS runtime", st.js_runtime.name || "none", st.js_runtime.path || "deno or node lets yt-dlp unlock YouTube's best formats"),
      ),
      h("div", { class: "btn-row", style: "margin-top:10px" }, adminAction("Check for updates", "api/admin/check-updates", { after: () => setTimeout(openAdmin, 200) })),
      app.update_available && app.notes ? h("details", { style: "margin-top:10px;font-size:0.8rem" },
        h("summary", { text: `What's new in ${app.latest}` }),
        h("pre", { style: "white-space:pre-wrap;font-size:0.75rem;color:var(--text-muted);margin-top:6px", text: app.notes.slice(0, 2500) })) : null,
    ),
    h("div", { class: "modal-section" },
      h("div", { class: "modal-section-title", text: "Server" }),
      h("div", { class: "stat-grid" },
        stat(st.jobs.running, "running"), stat(st.jobs.queued, "queued"), stat(st.jobs.completed_total, "completed"),
        stat(fmtBytes(st.tmp_bytes) || "0 B", "temp files"), stat(st.disk ? fmtBytes(st.disk.free) : "?", "disk free"),
        stat(fmtBytes(st.relay.bytes_total) || "0 B", "relayed"), stat(st.relay.active, "active relays"),
        stat(st.bandwidth.limit_mb ? `${st.bandwidth.limit_mb} MB/s` : "unlimited", "bandwidth cap"),
        stat(`${fmtBytes(st.bandwidth.current) || "0 B"}/s`, "sending now"),
        stat(fmtEta(st.uptime), "uptime")),
      h("p", { style: "margin:10px 0 0;font-size:0.78rem" },
        `Processing: ${st.processing.mode}` + (st.processing.mode === "choice" ? ` (default ${st.processing.default})` : "") +
        ` · relay ${st.processing.relay ? "on" : "off"} · fallback ${st.processing.fallback ? "on" : "off"} · ffmpeg.wasm from ${st.processing.wasm}` +
        (st.bandwidth.limit_mb ? ` · each server download ≤ ${+st.bandwidth.per_download_mb.toFixed(2)} MB/s` : "")),
      h("p", { style: "margin:4px 0 0;font-size:0.75rem", class: "mono", text: `config: ${st.config_path || "(defaults)"} · data: ${st.data_path}` }),
    ),
    h("div", { class: "modal-section" },
      h("div", { class: "modal-section-title", text: `Recent server downloads (${jobList.length})` }),
      jobList.length ? h("div", { class: "admin-jobs" }, jobList.slice(0, 40).map((jj) => h("div", {},
        h("span", { text: jj.status, style: `color:var(--${jj.status === "done" ? "green" : jj.status === "error" ? "red" : "text-muted"})` }),
        h("span", { class: "t", text: jj.title || jj.host, title: `${jj.title} — ${jj.host} — ${jj.client}` }),
        h("span", { text: jj.progress != null && jj.status === "running" ? `${Math.round(jj.progress * 100)}%` : jj.format }),
        jj.status === "running" || jj.status === "queued" ? adminAction("×", `api/admin/jobs/${jj.id}/cancel`) : null,
      ))) : h("p", { text: "None right now." }),
    ),
    settingsSection(),
    securitySection(st.security),
    h("div", { class: "btn-row" },
      adminAction("Clear finished downloads", "api/admin/cleanup"),
      adminAction("Restart server", "api/admin/restart", { confirmText: "Restart the server? Running downloads will be stopped.",
        after: () => { alertBox($("admin-msg"), "info", "Restarting… the page will reload.", true); waitForRestart(); } }),
      h("button", { class: "btn btn-small btn-secondary", type: "button", text: "Log out", onclick: async () => {
        await api("api/admin/logout", { method: "POST" }).catch(() => {});
        CONFIG.auth.admin = false;
        CONFIG.auth.admin_setup = null;
        $("admin-panel").replaceChildren();
        $("admin-dot").classList.add("hidden");
        openAdmin();
      } }),
    ),
  );
}

function stat(value, label) { return h("div", { class: "stat" }, h("b", { text: value ?? "0" }), h("span", { text: label })); }

async function waitForRestart() {
  await new Promise((r) => setTimeout(r, 2500));
  for (let i = 0; i < 40; i++) {
    try { const r = await fetch("api/health", { cache: "no-store" }); if (r.ok) { location.reload(); return; } } catch { /* down */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// ── init ────────────────────────────────────────────────────────────────
function bind() {
  $("url-form").addEventListener("submit", (e) => { e.preventDefault(); fetchInfo(false); });
  $("url-input").addEventListener("paste", () => setTimeout(() => {
    if (/^\s*https?:\/\//i.test($("url-input").value) || /https?:\/\//i.test($("url-input").value)) fetchInfo(false);
  }, 0));
  const paste = $("paste-btn");
  if (window.isSecureContext && navigator.clipboard?.readText) {
    paste.addEventListener("click", async () => {
      try {
        const t = await navigator.clipboard.readText();
        if (t) { $("url-input").value = extractUrl(t); fetchInfo(false); }
      } catch { $("url-input").focus(); }
    });
  } else {
    paste.classList.add("hidden");
    $("url-input").style.paddingRight = "14px";
  }
  $("tab-video").addEventListener("click", () => { setKind("video"); updateDownloadButton(); });
  $("tab-audio").addEventListener("click", () => { setKind("audio"); updateDownloadButton(); });
  $("embed-toggle").addEventListener("change", (e) => { S.embed = e.target.checked; savePrefs(); });
  $("zip-toggle").addEventListener("change", (e) => { S.zip = e.target.checked; savePrefs(); });
  document.querySelectorAll("[data-proc]").forEach((b) => b.addEventListener("click", () => {
    S.processing = b.dataset.proc;
    savePrefs();
    renderProcessing();
    renderZipRow();
  }));
  $("pl-all").addEventListener("click", () => {
    S.selected = new Set(S.info.entries.filter((e) => !e.too_long).slice(0, S.info.max_items).map((e) => e.index));
    renderPlaylist();
  });
  $("pl-none").addEventListener("click", () => { S.selected.clear(); renderPlaylist(); });
  $("dl-btn").addEventListener("click", startDownload);
  $("clear-finished").addEventListener("click", () => {
    for (const j of [...jobs.values()]) if (!["queued", "running"].includes(j.status)) removeJob(j);
  });
  $("open-sites").addEventListener("click", () => $("sites-modal").showModal());
  $("open-settings").addEventListener("click", () => openSettings("main"));
  $("open-admin").addEventListener("click", openAdmin);
  $("admin-back").addEventListener("click", () => showSettingsPage("main", "back"));
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
  bindAppearance();
  $("admin-login").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("api/admin/login", { method: "POST", body: { password: $("admin-pass").value, code: $("admin-code").value } });
      $("admin-pass").value = "";
      $("admin-code").value = "";
      $("admin-code-row").classList.add("hidden");
      clear($("admin-login-alert"));
      await refreshConfig();
      $("admin-panel").replaceChildren();
      openAdmin();
    } catch (err) {
      if (err.data?.need_code) {
        $("admin-code-row").classList.remove("hidden");
        $("admin-code").focus();
        alertBox($("admin-login-alert"), "info", err.message);
      } else {
        alertBox($("admin-login-alert"), err.status === 429 ? "warn" : "error", err.message);
      }
    }
  });
  for (const dlg of document.querySelectorAll("dialog.modal, dialog.sheet")) {
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  }
}

// ── settings sheet: appearance ──────────────────────────────────────────
const AP = loadAppearance();
const serverTheme = () => ({ seed: isHex(CONFIG?.ui?.accent) ? CONFIG.ui.accent : DEFAULT_SEED,
  style: STYLES[CONFIG?.ui?.style] ? CONFIG.ui.style : DEFAULT_STYLE });

function setAppearance(patch) {
  Object.assign(AP, patch);
  for (const k of Object.keys(AP)) if (AP[k] == null) delete AP[k];
  saveAppearance(AP);
  applyAppearance(AP, serverTheme());
  renderAppearance();
}

function renderAppearance() {
  const mode = AP.mode || "system";
  document.querySelectorAll("#pref-mode [data-mode]").forEach((b) => b.setAttribute("aria-checked", b.dataset.mode === mode));
  const contrast = AP.contrast || "standard";
  document.querySelectorAll("#pref-contrast [data-contrast]").forEach((b) => b.setAttribute("aria-checked", b.dataset.contrast === contrast));
  $("pref-motion").checked = !!AP.reduceMotion;

  const st = serverTheme();
  const seed = (AP.seed || st.seed).toLowerCase();
  const style = AP.style || st.style;
  const wrap = $("pref-swatches");
  const custom = !PRESETS.some((p) => p.hex === seed);
  wrap.replaceChildren(
    ...PRESETS.map((p) => {
      const c = swatchColor(p.hex, style);
      return h("button", { type: "button", class: "swatch" + (p.hex === seed ? " selected" : ""), role: "radio",
        "aria-checked": p.hex === seed, "aria-label": p.name, title: p.name,
        style: `--sw-1:${c.primary};--sw-2:${c.secondary};--sw-3:${c.tertiary}`,
        onclick: () => setAppearance({ seed: p.hex === st.seed ? null : p.hex }) }, h("i"), h("i"));
    }),
    h("label", { class: "swatch custom" + (custom ? " selected" : ""), title: "Pick any color" },
      h("input", { type: "color", value: seed, "aria-label": "Custom color",
        oninput: (e) => { const v = e.target.value; cancelAnimationFrame(renderAppearance._raf);
          renderAppearance._raf = requestAnimationFrame(() => setAppearance({ seed: v })); } })),
  );
  $("pref-color-note").textContent = custom ? `Custom ${seed.toUpperCase()}` : "Every color in the app is generated from this one";
  $("pref-styles").replaceChildren(...Object.entries(STYLES).map(([id, sDef]) =>
    h("button", { type: "button", class: "chip" + (id === style ? " selected" : ""), role: "radio", "aria-checked": id === style,
      text: sDef.label, title: sDef.note, onclick: () => setAppearance({ style: id === st.style ? null : id }) })));
  $("pref-style-note").textContent = STYLES[style]?.note || "";
}

function bindAppearance() {
  document.querySelectorAll("#pref-mode [data-mode]").forEach((b) => b.addEventListener("click", () =>
    setAppearance({ mode: b.dataset.mode === "system" ? null : b.dataset.mode })));
  document.querySelectorAll("#pref-contrast [data-contrast]").forEach((b) => b.addEventListener("click", () =>
    setAppearance({ contrast: b.dataset.contrast === "standard" ? null : b.dataset.contrast })));
  $("pref-motion").addEventListener("change", (e) => setAppearance({ reduceMotion: e.target.checked || null }));
  $("pref-autosave").addEventListener("change", (e) => { UI.autosave = e.target.checked; saveUI(); });
  document.querySelectorAll("#pref-proc [data-pproc]").forEach((b) => b.addEventListener("click", () => {
    S.processing = b.dataset.pproc;
    savePrefs();
    renderProcessing();
    renderZipRow();
    renderDownloadPrefs();
  }));
  $("pref-clear-history").addEventListener("click", () => {
    let n = 0;
    for (const j of [...jobs.values()]) if (!["queued", "running"].includes(j.status)) { removeJob(j); n++; }
    toast(n ? `Cleared ${n} finished download${n === 1 ? "" : "s"}` : "Nothing to clear");
  });
}

function renderDownloadPrefs() {
  $("pref-autosave").checked = UI.autosave;
  const { choice } = CONFIG ? procAvailability() : { choice: false };
  $("pref-proc-item").classList.toggle("hidden", !choice);
  document.querySelectorAll("#pref-proc [data-pproc]").forEach((b) => b.setAttribute("aria-checked", b.dataset.pproc === S.processing));
}

// ── ripples (M3 touch feedback) ─────────────────────────────────────────
function bindRipples() {
  const SEL = ".btn, .icon-btn, .chip, .connected > button, .seg > button, .list-item.sl, .job-x, .pill";
  document.addEventListener("pointerdown", (e) => {
    const t = e.target.closest(SEL);
    if (!t || t.disabled || document.documentElement.dataset.motion === "reduced") return;
    const r = t.getBoundingClientRect();
    const size = Math.hypot(r.width, r.height) * 2;
    const dot = document.createElement("span");
    dot.className = "ripple";
    dot.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX - r.left - size / 2}px;top:${e.clientY - r.top - size / 2}px`;
    t.append(dot);
    dot.addEventListener("animationend", () => dot.remove(), { once: true });
  }, { passive: true });
}

function updateAdminDot() {
  const u = CONFIG.update, y = CONFIG.ytdlp_update;
  $("admin-dot").classList.toggle("hidden", !(u?.update_available || y?.update_available));
  const dot = !!(u?.update_available || y?.update_available);
  $("admin-entry-dot").classList.toggle("hidden", !dot);
  $("admin-entry-note").textContent = u?.update_available ? `Update available: ${u.latest}` : "Updates, server settings, security";
}

async function init() {
  installShapes();
  applyAppearance(AP, {});
  bindRipples();
  const bar = $("appbar");
  addEventListener("scroll", () => bar.classList.toggle("scrolled", scrollY > 4), { passive: true });
  bind();
  updateEmpty();
  try {
    CONFIG = await api("api/config");
  } catch (e) {
    alertBox($("fetch-alert"), "error", "Couldn't reach the server. Is it running?");
    return;
  }
  engine = new BrowserEngine(CONFIG);
  S.processing = initialProcessing();
  $("version-badge").replaceChildren(h("span", { class: "msr", text: "bolt" }), `v${CONFIG.version} · yt-dlp ${CONFIG.ytdlp_version || "missing"}`);
  if (CONFIG.auth.admin_enabled) $("admin-entry-group").classList.remove("hidden");
  applyAppearance(AP, serverTheme());
  $("about-version").textContent = CONFIG.version;
  $("about-ytdlp").textContent = `yt-dlp ${CONFIG.ytdlp_version || "not installed"} · processing: ${CONFIG.processing.mode}`;
  updateAdminDot();
  if (CONFIG.limits.allowed_sites?.length) {
    const p = $("sites-restricted");
    p.textContent = `This server only allows: ${CONFIG.limits.allowed_sites.join(", ")}.`;
    p.classList.remove("hidden");
  }
  if (!CONFIG.ytdlp_ok) alertBox($("fetch-alert"), "warn", "The server is still installing yt-dlp. Give it a minute.");

  restoreServerJobs();

  const params = new URLSearchParams(location.search);
  const shared = params.get("url") || params.get("text");
  if (shared) {
    $("url-input").value = extractUrl(shared);
    history.replaceState(null, "", location.pathname);
    fetchInfo(false);
  } else {
    $("url-input").focus({ preventScroll: true });
  }
}

init();
