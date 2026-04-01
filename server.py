from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import subprocess
import json
import os
import re
import io
import shutil
import zipfile
import platform
import threading
import time
import uuid
import logging

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=".")
CORS(app)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR   = os.path.join(SCRIPT_DIR, ".tmp")
os.makedirs(TEMP_DIR, exist_ok=True)

AUDIO_BITRATES     = ["320k", "192k", "128k", "96k"]
VIDEO_FORMATS      = {"mp4", "webm", "mkv"}
AUDIO_FORMATS      = {"mp3", "flac", "wav", "aac", "opus"}
INFO_TIMEOUT       = 30
DOWNLOAD_TIMEOUT   = 600
PLAYLIST_TIMEOUT   = 7200   # 2 hours for large playlists
CLEANUP_DELAY      = 120
TMP_PURGE_INTERVAL = 3600   # purge .tmp contents every hour

# ── Binary detection ──────────────────────────────────────────────────────────
def _find_bin(win_name, unix_name):
    local = os.path.join(SCRIPT_DIR, win_name if platform.system() == "Windows" else unix_name)
    if os.path.isfile(local):
        return local
    found = shutil.which(unix_name)
    return found or unix_name

YTDLP_BIN  = _find_bin("yt-dlp.exe",  "yt-dlp")
FFMPEG_BIN = _find_bin("ffmpeg.exe",  "ffmpeg")
FFMPEG_DIR = os.path.dirname(os.path.abspath(FFMPEG_BIN)) if os.path.isfile(FFMPEG_BIN) else None

log.info("yt-dlp  : %s", YTDLP_BIN)
log.info("ffmpeg  : %s", FFMPEG_BIN)

# ── Helpers ───────────────────────────────────────────────────────────────────
def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def cleanup_later(path, delay=CLEANUP_DELAY):
    """Delete a file or directory tree after `delay` seconds."""
    def _rm():
        time.sleep(delay)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except Exception:
                pass
    threading.Thread(target=_rm, daemon=True).start()

def purge_tmp():
    """Remove everything inside TEMP_DIR."""
    count = 0
    for name in os.listdir(TEMP_DIR):
        p = os.path.join(TEMP_DIR, name)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            count += 1
        except Exception:
            pass
    return count

def _tmp_purge_loop():
    """Background thread: purge .tmp every TMP_PURGE_INTERVAL seconds."""
    while True:
        time.sleep(TMP_PURGE_INTERVAL)
        removed = purge_tmp()
        if removed:
            log.info("Periodic cleanup: removed %d item(s) from .tmp", removed)

threading.Thread(target=_tmp_purge_loop, daemon=True).start()

def classify_error(stderr):
    s = (stderr or "").lower()
    if "private video" in s or "this video is private" in s:
        return "This video is private."
    if "sign in" in s or "login required" in s:
        return "This video requires sign-in."
    if "video unavailable" in s or "this video is not available" in s:
        return "This video is unavailable."
    if "unsupported url" in s or "not a valid url" in s:
        return "That URL isn't supported by yt-dlp."
    if "copyright" in s:
        return "This video is blocked due to copyright."
    if "age" in s and "restricted" in s:
        return "This video is age-restricted."
    if any(k in s for k in ("no space left", "disk full", "errno 28", "not enough space",
                             "oserror", "write error", "permission denied")):
        return "STORAGE_ERROR: Not enough disk space or write error."
    return "Could not process that URL. Check it and try again."

def build_video_cmd(base, fmt, quality, url):
    """Return yt-dlp command list for a video download."""
    h = quality.rstrip("p") if quality and quality != "best" else None
    if fmt == "webm":
        if h:
            fmt_str = (
                f"bestvideo[height<={h}][ext=webm]+bestaudio[ext=webm]"
                f"/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
            )
        else:
            fmt_str = "bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio/best"
        return base + ["-f", fmt_str, "--merge-output-format", "webm", url]
    elif fmt == "mkv":
        if h:
            fmt_str = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
        else:
            fmt_str = "bestvideo+bestaudio/best"
        return base + ["-f", fmt_str, "--merge-output-format", "mkv", url]
    else:  # mp4 (default)
        if h:
            fmt_str = (
                f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
            )
        else:
            fmt_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        return base + ["-f", fmt_str, "--merge-output-format", "mp4", url]

def build_audio_cmd(base, fmt, bitrate, url):
    """Return yt-dlp command list for an audio extraction."""
    af = fmt if fmt in AUDIO_FORMATS else "mp3"
    return base + ["-x", "--audio-format", af, "--audio-quality", bitrate, url]

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(SCRIPT_DIR, "index.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided."}), 400

    # Detect playlist vs single video using --flat-playlist --dump-single-json
    try:
        r = subprocess.run(
            [YTDLP_BIN, "--flat-playlist", "--dump-single-json", "--no-warnings", url],
            capture_output=True, text=True, timeout=INFO_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching info. Try again."}), 504
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp not found. Is it installed?"}), 500

    if r.returncode != 0:
        return jsonify({"error": classify_error(r.stderr)}), 400

    try:
        meta = json.loads(r.stdout)
    except json.JSONDecodeError:
        return jsonify({"error": "Couldn't parse metadata. URL may be unsupported."}), 500

    # ── Playlist ──────────────────────────────────────────────────
    if meta.get("_type") == "playlist":
        entries = meta.get("entries") or []
        # Prefer playlist-level thumbnail, then thumbnails list, then first entry
        thumb = meta.get("thumbnail") or ""
        if not thumb:
            thumbs = meta.get("thumbnails")
            if thumbs:
                thumb = thumbs[-1].get("url", "")
        if not thumb and entries:
            thumb = entries[0].get("thumbnail") or entries[0].get("thumbnails", [{}])[-1].get("url", "")
        return jsonify({
            "type":      "playlist",
            "title":     meta.get("title") or meta.get("id", "Playlist"),
            "uploader":  meta.get("uploader") or meta.get("channel", ""),
            "count":     len(entries),
            "thumbnail": thumb,
        })

    # ── Single video ──────────────────────────────────────────────
    # Re-fetch with full format info if we only got a flat entry
    if "formats" not in meta:
        try:
            r2 = subprocess.run(
                [YTDLP_BIN, "--no-playlist", "--dump-json", "--no-warnings", url],
                capture_output=True, text=True, timeout=INFO_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Timed out fetching format info."}), 504
        if r2.returncode != 0:
            return jsonify({"error": classify_error(r2.stderr)}), 400
        try:
            meta = json.loads(r2.stdout)
        except json.JSONDecodeError:
            return jsonify({"error": "Couldn't parse format data."}), 500

    heights = sorted(
        {f["height"] for f in meta.get("formats", [])
         if f.get("height") and f.get("vcodec", "none") != "none"},
        reverse=True,
    )
    video_qualities = [f"{h}p" for h in heights] or ["best"]

    duration_s   = int(meta.get("duration") or 0)
    mins, secs   = divmod(duration_s, 60)

    return jsonify({
        "type":            "video",
        "title":           meta.get("title", "Unknown"),
        "uploader":        meta.get("uploader") or meta.get("channel", "Unknown"),
        "thumbnail":       meta.get("thumbnail", ""),
        "duration":        f"{mins}:{secs:02d}",
        "video_qualities": video_qualities,
        "audio_qualities": AUDIO_BITRATES,
    })


@app.route("/api/download", methods=["POST"])
def api_download():
    data    = request.get_json(silent=True) or {}
    url     = (data.get("url")     or "").strip()
    fmt     = (data.get("format")  or "mp4").lower()
    quality = (data.get("quality") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    work_dir = os.path.join(TEMP_DIR, uuid.uuid4().hex)
    os.makedirs(work_dir, exist_ok=True)
    out_tmpl = os.path.join(work_dir, "%(title)s.%(ext)s")

    base = [YTDLP_BIN, "--no-playlist", "--no-warnings", "-o", out_tmpl]
    if FFMPEG_DIR:
        base += ["--ffmpeg-location", FFMPEG_DIR]

    if fmt in AUDIO_FORMATS:
        bitrate = quality if quality in AUDIO_BITRATES else "192k"
        cmd = build_audio_cmd(base, fmt, bitrate, url)
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
    else:
        cmd = build_video_cmd(base, fmt, quality, url)
        mime = "video/mp4" if fmt == "mp4" else "video/webm" if fmt == "webm" else "video/x-matroska"

    log.info("Download  fmt=%s  quality=%s  url=%s", fmt, quality, url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": "Download timed out. The video may be too large."}), 504
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp not found. Is it installed?"}), 500

    if result.returncode != 0:
        log.error("yt-dlp stderr: %s", result.stderr[:400])
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": classify_error(result.stderr)}), 500

    files = [os.path.join(work_dir, f) for f in os.listdir(work_dir)]
    if not files:
        return jsonify({"error": "Download finished but no output file was found."}), 500

    filepath = max(files, key=os.path.getmtime)
    filename  = sanitize_filename(os.path.basename(filepath))
    cleanup_later(work_dir)

    return send_file(filepath, mimetype=mime, as_attachment=True, download_name=filename)


@app.route("/api/download-playlist", methods=["POST"])
def api_download_playlist():
    data    = request.get_json(silent=True) or {}
    url     = (data.get("url")     or "").strip()
    fmt     = (data.get("format")  or "mp3").lower()
    quality = (data.get("quality") or "192k").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    work_dir = os.path.join(TEMP_DIR, uuid.uuid4().hex)
    os.makedirs(work_dir, exist_ok=True)
    out_tmpl = os.path.join(work_dir, "%(playlist_index)03d - %(title)s.%(ext)s")

    # --ignore-errors skips unavailable/private videos instead of aborting
    base = [YTDLP_BIN, "--no-warnings", "--ignore-errors", "-o", out_tmpl]
    if FFMPEG_DIR:
        base += ["--ffmpeg-location", FFMPEG_DIR]

    if fmt in AUDIO_FORMATS:
        bitrate = quality if quality in AUDIO_BITRATES else "192k"
        cmd = build_audio_cmd(base, fmt, bitrate, url)
    else:
        cmd = build_video_cmd(base, fmt, quality, url)

    log.info("Playlist download  fmt=%s  quality=%s  url=%s", fmt, quality, url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PLAYLIST_TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": "Playlist download timed out."}), 504
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp not found. Is it installed?"}), 500

    # Check files first — partial success (some unavailable) is still a success
    files = [f for f in os.listdir(work_dir) if not f.endswith(".zip")]
    if not files:
        # Nothing downloaded at all — now we care about the exit code
        log.error("yt-dlp playlist stderr: %s", result.stderr[:400])
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": classify_error(result.stderr)}), 500

    if result.returncode != 0:
        log.warning("Playlist finished with errors (skipped some videos): %s",
                    result.stderr[:200])

    # Pack everything into a zip and stream it
    zip_path = os.path.join(work_dir, "playlist.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for fname in sorted(files):
            zf.write(os.path.join(work_dir, fname), fname)

    log.info("Playlist zipped: %d files → %s", len(files), zip_path)
    cleanup_later(work_dir)

    return send_file(zip_path, mimetype="application/zip", as_attachment=True,
                     download_name="playlist.zip")


@app.route("/api/clear-tmp", methods=["POST"])
def api_clear_tmp():
    removed = purge_tmp()
    log.info("Manual cleanup: removed %d item(s) from .tmp", removed)
    return jsonify({"cleared": removed})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("  ┌──────────────────────────────┐")
    print("  │  YTDLUI v1.2.0              │")
    print("  │  http://localhost:5000       │")
    print("  └──────────────────────────────┘")
    print()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
