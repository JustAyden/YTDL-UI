"""Everything that talks to yt-dlp: discovery, format selection, metadata, errors."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import APP_DIR, FROZEN, Settings

log = logging.getLogger("ytdlui")

IS_WINDOWS = platform.system() == "Windows"

# ── Formats offered in the UI ────────────────────────────────────────────────
VIDEO_FORMATS: dict[str, dict[str, Any]] = {
    "mp4":  {"label": "MP4",  "note": "Plays everywhere"},
    "webm": {"label": "WebM", "note": "VP9/AV1 + Opus"},
    "mkv":  {"label": "MKV",  "note": "Any codec, no re-encode"},
}
AUDIO_FORMATS: dict[str, dict[str, Any]] = {
    "mp3":  {"label": "MP3",  "lossy": True,  "note": "Most compatible"},
    "m4a":  {"label": "M4A",  "lossy": True,  "note": "AAC, usually no re-encode"},
    "opus": {"label": "Opus", "lossy": True,  "note": "Smallest, usually no re-encode"},
    "flac": {"label": "FLAC", "lossy": False, "note": "Lossless container"},
    "wav":  {"label": "WAV",  "lossy": False, "note": "Uncompressed"},
}
FORMAT_ALIASES = {"aac": "m4a", "ogg": "opus"}
AUDIO_BITRATES = [320, 256, 192, 128, 96]
DEFAULT_BITRATE = 192
PLAYLIST_HEIGHTS = [2160, 1440, 1080, 720, 480, 360]

MIME_TYPES = {
    "mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "opus": "audio/ogg", "flac": "audio/flac",
    "wav": "audio/wav", "zip": "application/zip",
}

# Browser processing can only fetch plain HTTP(S) files, not HLS/DASH segment lists.
_HTTP_ONLY = "[protocol^=http][protocol!*=dash]"


@dataclass
class Request:
    """A normalised download request (shared by server jobs and browser resolves)."""
    url: str
    kind: str = "video"            # video | audio
    format: str = "mp4"
    height: int | None = None      # None = best
    bitrate: int = DEFAULT_BITRATE
    embed: bool = True             # metadata + cover art
    playlist: bool = False
    items: list[int] = field(default_factory=list)   # 1-based playlist indices

    @classmethod
    def from_json(cls, data: dict[str, Any], max_items: int) -> Request:
        fmt = str(data.get("format") or "mp4").lower().strip()
        fmt = FORMAT_ALIASES.get(fmt, fmt)
        kind = str(data.get("kind") or "").lower()
        if fmt in AUDIO_FORMATS:
            kind = "audio"
        elif fmt in VIDEO_FORMATS:
            kind = "video"
        else:
            raise ValueError("Unsupported format.")

        quality = str(data.get("quality") or "").strip().lower()
        height = None
        bitrate = DEFAULT_BITRATE
        if kind == "video":
            m = re.fullmatch(r"(\d{3,4})p?", quality)
            if m:
                height = int(m.group(1))
        else:
            m = re.fullmatch(r"(\d{2,3})k?", quality)
            if m and int(m.group(1)) in AUDIO_BITRATES:
                bitrate = int(m.group(1))

        items: list[int] = []
        raw_items = data.get("items") or []
        if isinstance(raw_items, list):
            for x in raw_items:
                try:
                    n = int(x)
                except (TypeError, ValueError):
                    continue
                if 1 <= n <= 100000 and n not in items:
                    items.append(n)
        if len(items) > max_items:
            raise ValueError(f"Pick at most {max_items} playlist items.")

        embed = data.get("embed")
        if embed is None:
            embed = kind == "audio"
        return cls(url=str(data.get("url") or "").strip(), kind=kind, format=fmt,
                   height=height, bitrate=bitrate, embed=bool(embed),
                   playlist=bool(data.get("playlist")), items=sorted(items))

    @property
    def quality_label(self) -> str:
        if self.kind == "audio":
            return "lossless" if not AUDIO_FORMATS[self.format]["lossy"] else f"{self.bitrate}k"
        return f"{self.height}p" if self.height else "best"


# ── Format selection ─────────────────────────────────────────────────────────
def video_selector(fmt: str, height: int | None, http_only: bool = False) -> tuple[str, str]:
    """Return (-f selector, -S sort) for a video request."""
    p = _HTTP_ONLY if http_only else ""
    res = f"res:{height}," if height else ""
    if fmt == "webm":
        sel = f"bv*[ext=webm]{p}+ba[ext=webm]{p}/bv*{p}+ba{p}/b{p}"
        sort = f"{res}vcodec:vp9,acodec:opus"
    elif fmt == "mkv":
        sel = f"bv*{p}+ba{p}/b{p}"
        sort = res.rstrip(",") or "res"
    else:  # mp4 — favour H.264/AAC for compatibility, fall back to anything
        sel = f"bv*[ext=mp4]{p}+ba[ext=m4a]{p}/bv*{p}+ba{p}/b{p}"
        sort = f"{res}vcodec:h264,acodec:aac"
    return sel, sort


def audio_selector(fmt: str, http_only: bool = False) -> str:
    p = _HTTP_ONLY if http_only else ""
    if fmt == "m4a":
        return f"ba[ext=m4a]{p}/ba{p}/b{p}"
    if fmt == "opus":
        return f"ba[acodec=opus]{p}/ba{p}/b{p}"
    return f"ba{p}/b{p}"


def selection_args(req: Request, http_only: bool = False) -> list[str]:
    if req.kind == "audio":
        return ["-f", audio_selector(req.format, http_only)]
    sel, sort = video_selector(req.format, req.height, http_only)
    return ["-f", sel, "-S", sort]


def processing_args(req: Request) -> list[str]:
    """Post-processing for server-side downloads."""
    if req.kind == "audio":
        args = ["-x", "--audio-format", req.format]
        if AUDIO_FORMATS[req.format]["lossy"]:
            args += ["--audio-quality", f"{req.bitrate}K"]
        if req.embed:
            args += ["--embed-metadata"]
            if req.format in ("mp3", "m4a", "flac", "opus"):
                args += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
        return args
    args = ["--merge-output-format", req.format]
    if req.embed:
        args += ["--embed-metadata"]
        if req.format in ("mp4", "mkv"):
            args += ["--embed-thumbnail"]
    return args


# ── Errors ───────────────────────────────────────────────────────────────────
_ERRORS: list[tuple[tuple[str, ...], str]] = [
    (("confirm you're not a bot", "confirm you’re not a bot", "not a bot"),
     "YouTube is asking this server to prove it isn't a bot. Try again later — or, if you "
     "run this server, add a cookies file (see README › Troubleshooting)."),
    (("private video", "video is private"), "This video is private."),
    (("members-only", "join this channel"), "This video is for channel members only."),
    (("confirm your age", "age-restricted", "age restricted", "inappropriate for some users"),
     "This video is age-restricted. The server admin can add a cookies file to allow it."),
    (("sign in", "login required", "log in", "requires authentication"),
     "This video requires signing in."),
    (("drm",), "This video is DRM-protected and can't be downloaded."),
    (("not available in your country", "geo restrict", "geo-restrict", "blocked it in your country"),
     "This video isn't available in the server's country."),
    (("premieres in", "live event will begin", "this live event"),
     "This video hasn't started yet."),
    (("copyright",), "This video was blocked on copyright grounds."),
    (("video unavailable", "this video is not available", "has been removed", "does not exist"),
     "This video is unavailable."),
    (("unsupported url",), "That link isn't supported by yt-dlp."),
    (("http error 429", "too many requests"),
     "The site is rate-limiting this server. Try again in a few minutes."),
    (("requested format is not available", "no video formats found"),
     "That format isn't available for this video — try another quality or format."),
    (("file is larger than max-filesize",), "This file is larger than this server allows."),
    (("does not pass filter", "duration"), "This video is longer than this server allows."),
    (("no space left", "errno 28", "disk full", "not enough space"),
     "The server ran out of disk space. Try again later."),
    (("unable to download", "timed out", "connection reset", "temporary failure in name resolution",
      "getaddrinfo failed"), "Couldn't reach the site. Check the link or try again."),
]


def error_lines(output: str) -> list[str]:
    return [ln.strip() for ln in (output or "").splitlines() if ln.strip().startswith("ERROR:")]


def classify_error(output: str) -> str:
    lines = error_lines(output)
    text = ("\n".join(lines) if lines else output or "").lower()
    for needles, message in _ERRORS:
        if any(n in text for n in needles):
            return message
    return "Couldn't process that link. Check it and try again."


# ── Tool discovery ───────────────────────────────────────────────────────────



@dataclass
class Tooling:
    ytdlp_cmd: list[str] | None
    ytdlp_kind: str                 # custom | managed | pip | bundled | binary | missing
    ytdlp_location: str
    ffmpeg: str | None
    js_runtime: str | None          # deno | node | bun | None
    js_runtime_path: str | None


def _bin_dirs(settings: Settings) -> list[str]:
    dirs = [os.path.join(settings.data_path, "bin"), APP_DIR, os.path.join(APP_DIR, "bin")]
    if FROZEN:
        dirs.append(os.path.dirname(sys.executable))
    return dirs


def _local_bin(settings: Settings, name: str) -> str | None:
    exe = name + (".exe" if IS_WINDOWS else "")
    for d in _bin_dirs(settings):
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    return None


def managed_ytdlp_path(settings: Settings) -> str:
    """Where the updater keeps its own copy of the official yt-dlp release binary."""
    return os.path.join(settings.data_path, "bin", "yt-dlp" + (".exe" if IS_WINDOWS else ""))


def managed_ytdlp_cmd(path: str) -> list[str]:
    # The platform-independent release ("yt-dlp" zipapp) needs a Python to run it.
    if path.endswith(".pyz"):
        return [sys.executable if not FROZEN else "python3", path]
    return [path]


def discover(settings: Settings) -> Tooling:
    cmd: list[str] | None
    managed = managed_ytdlp_path(settings)
    managed_pyz = os.path.join(settings.data_path, "bin", "yt-dlp.pyz")
    if settings.ytdlp_path:
        cmd, kind, where = [settings.ytdlp_path], "custom", settings.ytdlp_path
    elif os.path.isfile(managed):
        cmd, kind, where = managed_ytdlp_cmd(managed), "managed", managed
    elif os.path.isfile(managed_pyz):
        cmd, kind, where = managed_ytdlp_cmd(managed_pyz), "managed", managed_pyz
    elif not FROZEN and importlib.util.find_spec("yt_dlp") is not None:
        cmd, kind, where = [sys.executable, "-m", "yt_dlp"], "pip", sys.executable
    elif FROZEN and importlib.util.find_spec("yt_dlp") is not None:
        cmd, kind, where = [sys.executable, "--run-ytdlp"], "bundled", sys.executable
    elif (p := _local_bin(settings, "yt-dlp")) or (p := shutil.which("yt-dlp")):
        cmd, kind, where = [p], "binary", p
    else:
        cmd, kind, where = None, "missing", ""

    ffmpeg = settings.ffmpeg_path or _local_bin(settings, "ffmpeg") or shutil.which("ffmpeg")

    js_name = js_path = None
    order = ["deno", "node", "bun"] if settings.js_runtime == "auto" else [settings.js_runtime]
    for name in order:
        if name == "none":
            break
        found = _local_bin(settings, name) or shutil.which(name)
        if found:
            js_name, js_path = name, found
            break
    return Tooling(cmd, kind, where, ffmpeg, js_name, js_path)


_VERSION_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})(?:\.(\d+))?")


def parse_ytdlp_version(s: str) -> tuple[int, ...] | None:
    m = _VERSION_RE.search(s or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups() if x is not None)


class YtDlp:
    """Builds and runs yt-dlp commands with the server's configuration applied."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._version: str | None = None
        self.refresh()

    def refresh(self) -> None:
        with self._lock:
            self.tools = discover(self.settings)
            self._version = None

    # ── info ────────────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return self.tools.ytdlp_cmd is not None

    def version(self) -> str | None:
        if self._version is None and self.available:
            try:
                r = subprocess.run(self.tools.ytdlp_cmd + ["--version"], capture_output=True,
                                   text=True, timeout=30, env=self.env())
                self._version = (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired, IndexError):
                self._version = None
        return self._version

    def ffmpeg_version(self) -> str | None:
        if not self.tools.ffmpeg:
            return None
        try:
            r = subprocess.run([self.tools.ffmpeg, "-version"], capture_output=True, text=True, timeout=15)
            m = re.search(r"ffmpeg version (\S+)", r.stdout or "")
            return m.group(1) if m else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        cache = self.settings.cache_path
        env.setdefault("XDG_CACHE_HOME", cache)
        env.setdefault("DENO_DIR", os.path.join(cache, "deno"))
        return env

    # ── command building ────────────────────────────────────────────────
    def base_args(self) -> list[str]:
        if not self.available:
            raise FileNotFoundError("yt-dlp is not installed")
        s = self.settings
        args = list(self.tools.ytdlp_cmd) + [
            "--ignore-config", "--no-warnings", "--color", "never",
            "--cache-dir", os.path.join(s.cache_path, "yt-dlp"),
        ]
        if self.tools.ffmpeg:
            args += ["--ffmpeg-location", self.tools.ffmpeg]
        rt = self.tools.js_runtime
        if rt and rt != "deno":
            v = parse_ytdlp_version(self.version() or "")
            if v and v >= (2025, 11, 12):
                args += ["--js-runtimes", f"{rt}:{self.tools.js_runtime_path}"]
        if s.cookies_file:
            args += ["--cookies", s.cookies_file]
        if s.proxy:
            args += ["--proxy", s.proxy]
        if s.max_duration_minutes > 0:
            args += ["--match-filter", f"!duration | duration <= {s.max_duration_minutes * 60}"]
        if s.extra_args:
            args += shlex.split(s.extra_args, posix=not IS_WINDOWS)
        return args

    # ── metadata ────────────────────────────────────────────────────────
    def run_json(self, extra: list[str], url: str, timeout: int = 60) -> dict[str, Any]:
        cmd = self.base_args() + extra + ["--dump-single-json", "--", url]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, env=self.env())
        except subprocess.TimeoutExpired:
            raise YtDlpError("The site took too long to answer. Try again.") from None
        except FileNotFoundError:
            raise YtDlpError("yt-dlp isn't installed on the server.") from None
        if r.returncode != 0 or not r.stdout.strip():
            log.info("yt-dlp failed: %s", " | ".join(error_lines(r.stderr))[:500] or r.stderr[-300:])
            raise YtDlpError(classify_error(r.stderr))
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            raise YtDlpError("Couldn't read the site's response. The link may be unsupported.") from None

    def info(self, url: str, no_playlist: bool = False) -> dict[str, Any]:
        args = ["--flat-playlist"]
        if no_playlist:
            args.append("--no-playlist")
        meta = self.run_json(args, url, timeout=60)
        return summarize_info(meta, url, self.settings)


class YtDlpError(RuntimeError):
    """A user-presentable yt-dlp failure."""


# ── Summaries for the UI ─────────────────────────────────────────────────────
def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _size(f: dict[str, Any]) -> int | None:
    v = f.get("filesize") or f.get("filesize_approx")
    return int(v) if v else None


def _thumb(meta: dict[str, Any]) -> str:
    t = meta.get("thumbnail") or ""
    if not t:
        thumbs = [x for x in (meta.get("thumbnails") or []) if x.get("url")]
        if thumbs:
            t = thumbs[-1]["url"]
    return t if str(t).startswith(("https://", "http://")) else ""


def url_has_single_video(url: str) -> bool:
    """True for links like youtube.com/watch?v=X&list=Y that point at one video *and* a list."""
    q = parse_qs(urlparse(url).query)
    return "list" in q and "v" in q


def summarize_info(meta: dict[str, Any], url: str, settings: Settings) -> dict[str, Any]:
    max_dur = settings.max_duration_minutes * 60
    if meta.get("_type") in ("playlist", "multi_video"):
        entries = []
        for i, e in enumerate(meta.get("entries") or [], start=1):
            if not isinstance(e, dict):
                continue
            eu = e.get("url") or e.get("webpage_url") or ""
            if eu and not str(eu).startswith(("http://", "https://")):
                eu = e.get("webpage_url") or ""
            entries.append({
                "index": e.get("playlist_index") or i,
                "id": e.get("id"),
                "title": e.get("title") or e.get("id") or f"Item {i}",
                "duration": e.get("duration"),
                "duration_str": _fmt_duration(e.get("duration")),
                "uploader": e.get("uploader") or e.get("channel") or "",
                "url": eu,
                "too_long": bool(max_dur and e.get("duration") and e["duration"] > max_dur),
            })
        thumb = _thumb(meta)
        if not thumb and entries:
            first = (meta.get("entries") or [{}])[0] or {}
            thumb = _thumb(first) if isinstance(first, dict) else ""
        return {
            "type": "playlist",
            "id": meta.get("id"),
            "title": meta.get("title") or meta.get("id") or "Playlist",
            "uploader": meta.get("uploader") or meta.get("channel") or "",
            "thumbnail": thumb,
            "extractor": meta.get("extractor_key") or meta.get("extractor") or "",
            "count": len(entries),
            "entries": entries,
            "max_items": settings.max_playlist_items,
            "single_available": url_has_single_video(url),
            "video_qualities": [{"label": f"{h}p", "height": h, "size": None} for h in PLAYLIST_HEIGHTS],
            "has_video": True,
            "has_audio": True,
        }

    formats = [f for f in (meta.get("formats") or []) if isinstance(f, dict)]
    if not formats and meta.get("url"):
        formats = [meta]
    videos = [f for f in formats if f.get("vcodec") not in (None, "none") or
              (f.get("vcodec") is None and f.get("height"))]
    audios = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in ("none",)]
    best_audio = max(audios, key=lambda f: (f.get("abr") or f.get("tbr") or 0), default=None)
    audio_size = _size(best_audio) if best_audio else None

    by_height: dict[int, int | None] = {}
    for f in videos:
        h = f.get("height")
        if not h:
            continue
        size = _size(f)
        if size and f.get("acodec") in (None, "none") and audio_size:
            size += audio_size
        prev = by_height.get(h)
        if prev is None or (size and size > prev):
            by_height[h] = size
    qualities = [{"label": f"{h}p", "height": h, "size": by_height[h]}
                 for h in sorted(by_height, reverse=True)]

    duration = meta.get("duration")
    too_long = bool(max_dur and duration and duration > max_dur)
    has_video = bool(videos) or (not audios and bool(formats))
    return {
        "type": "video",
        "id": meta.get("id"),
        "title": meta.get("title") or "Untitled",
        "uploader": meta.get("uploader") or meta.get("channel") or meta.get("creator") or "",
        "thumbnail": _thumb(meta),
        "extractor": meta.get("extractor_key") or meta.get("extractor") or "",
        "duration": duration,
        "duration_str": _fmt_duration(duration),
        "is_live": bool(meta.get("is_live")),
        "too_long": too_long,
        "max_duration_minutes": settings.max_duration_minutes,
        "webpage_url": meta.get("webpage_url") or url,
        "video_qualities": qualities,
        "audio_size": audio_size,
        "has_video": has_video,
        "has_audio": bool(audios) or has_video,
    }


def media_metadata(meta: dict[str, Any]) -> dict[str, str]:
    """Tags a browser-side ffmpeg run should write (mirrors yt-dlp --embed-metadata)."""
    out = {
        "title": meta.get("track") or meta.get("title") or "",
        "artist": meta.get("artist") or meta.get("creator") or meta.get("uploader") or meta.get("channel") or "",
        "album": meta.get("album") or "",
        "date": str(meta.get("release_year") or (meta.get("upload_date") or "")[:4] or ""),
        "comment": meta.get("webpage_url") or "",
    }
    return {k: str(v)[:500] for k, v in out.items() if v}


_FS_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, ext: str = "", limit: int = 180) -> str:
    name = _FS_BAD.sub("", name or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if len(name.encode("utf-8")) > limit:
        name = name.encode("utf-8")[:limit].decode("utf-8", "ignore").rstrip()
    name = name or "download"
    return f"{name}.{ext}" if ext else name


def now() -> float:
    return time.time()
