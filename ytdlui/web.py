"""Flask application: routes, auth, security headers and background services."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import sys
import threading
import time
from functools import wraps
from typing import Any, Callable
from urllib.parse import quote

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory, session

from . import __version__
from .config import (ADMIN_EDITABLE, APP_DIR, FIELD_MAP, ConfigError, Settings, coerce_setting,
                     read_admin_overrides, write_admin_overrides)
from .jobs import JobManager, JobRejected
from .relay import FallbackNeeded, Relay, RelayError, TokenStore, resolve
from .auth import (AdminStore, LoginGuard, password_problem, qr_svg_data_uri, totp_uri)
from .security import RateLimiter, URLRejected, check_url, safe_equals
from .throttle import MB, Bandwidth, ytdlp_rate
from .updater import (AppUpdater, YtdlpUpdater, cleanup_old_binaries, download, ffmpeg_asset,
                      install_ffmpeg, restart_process)
from .ytdlp import (AUDIO_BITRATES, AUDIO_FORMATS, DEFAULT_BITRATE, MIME_TYPES, VIDEO_FORMATS,
                    Request, YtDlp, YtDlpError, safe_filename)
from .ziputil import stream_zip

log = logging.getLogger("ytdlui")

if getattr(sys, "frozen", False):
    STATIC_DIR = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "static")
else:
    STATIC_DIR = os.path.join(APP_DIR, "static")

FFMPEG_CORE_VERSION = "0.12.10"
FFMPEG_CORE_FILES = {
    "ffmpeg-core.js": "67a48f11645f85439f3fde4f2119042c16b374b910206b7a7a24f342e28dcae3",
    "ffmpeg-core.wasm": "9f57947a5bd530d8f00c5b3f2cb2a3492faa7e5d823315342d6a8656d0a6b7b7",
}
FFMPEG_CDN = f"https://cdn.jsdelivr.net/npm/@ffmpeg/core@{FFMPEG_CORE_VERSION}/dist/esm/"

OPEN_PATHS = ("/api/health", "/api/login", "/static/css/", "/static/img/", "/static/fonts/", "/static/js/login.js",
              "/static/js/theme-boot.js", "/static/js/shapes.js",
              "/manifest.webmanifest", "/favicon.ico")


class Services:
    """Everything the routes need, attached to ``app.extensions['ytdlui']``."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.started = time.time()
        self.ytdlp = YtDlp(settings)
        self.jobs = JobManager(settings, self.ytdlp)
        self.tokens = TokenStore()
        self.bandwidth = Bandwidth(settings.bandwidth_limit)
        self.relay = Relay(settings, self.tokens, self.bandwidth)
        self.limiter = RateLimiter()
        self.guard = LoginGuard(os.path.join(settings.data_path, "lockouts.json"),
                                settings.login_max_attempts, settings.login_lockout_minutes)
        self.admin = AdminStore(settings.data_path, settings.admin_password) if settings.admin_enabled else None
        if self.admin and self.admin.generated_password:
            bar = "=" * 64
            log.warning("\n%s\n  Initial admin password: %s\n  (also in %s)\n  Log in via the ⚙ button; "
                        "you'll set up 2FA and choose a new password.\n%s",
                        bar, self.admin.generated_password, self.admin.initial_file, bar)
        self.ytdlp_updater = YtdlpUpdater(settings, self.ytdlp, self.jobs.idle)
        self.app_updater = AppUpdater(settings)
        self.stop = threading.Event()
        self.wasm_state = "cdn" if settings.wasm_source == "cdn" else "pending"
        self.ffmpeg_install: dict[str, Any] = {"state": "idle"}

    def start_background(self) -> None:
        s = self.settings
        cleanup_old_binaries(s)
        threading.Thread(target=self.ytdlp_updater.loop, args=(self.stop,), name="ytdlp-updater",
                         daemon=True).start()
        if s.update_check:
            threading.Thread(target=self.app_updater.loop, args=(self.stop,), name="app-updater",
                             daemon=True).start()
        if s.wasm_source == "local":
            threading.Thread(target=self.fetch_wasm, name="wasm-fetch", daemon=True).start()
        if (s.ffmpeg_auto_install and not self.ytdlp.tools.ffmpeg and s.server_downloads_enabled
                and ffmpeg_asset()):
            threading.Thread(target=self.install_ffmpeg, name="ffmpeg-install", daemon=True).start()

    def install_ffmpeg(self) -> dict[str, Any]:
        if self.ffmpeg_install.get("state") == "running":
            return self.ffmpeg_install
        self.ffmpeg_install = {"state": "running", "done": 0, "total": 0}
        log.info("ffmpeg not found — downloading a static build into %s/bin …", self.settings.data_path)

        def prog(done: int, total: int) -> None:
            self.ffmpeg_install.update(done=done, total=total)
        try:
            path = install_ffmpeg(self.settings, prog)
            self.ytdlp.refresh()
            self.ffmpeg_install = {"state": "done", "path": path}
            log.info("ffmpeg installed: %s", path)
        except Exception as exc:
            self.ffmpeg_install = {"state": "error", "error": str(exc)}
            log.warning("ffmpeg install failed: %s", exc)
        return self.ffmpeg_install

    def fetch_wasm(self) -> None:
        import hashlib
        os.makedirs(self.settings.vendor_path, exist_ok=True)
        try:
            for name, sha in FFMPEG_CORE_FILES.items():
                dest = os.path.join(self.settings.vendor_path, name)
                if os.path.isfile(dest):
                    h = hashlib.sha256()
                    with open(dest, "rb") as fh:
                        for buf in iter(lambda: fh.read(1 << 20), b""):
                            h.update(buf)
                    if h.hexdigest() == sha:
                        continue
                got = download(FFMPEG_CDN + name, dest + ".part", timeout=120)
                if got != sha:
                    os.remove(dest + ".part")
                    raise RuntimeError(f"checksum mismatch for {name}")
                os.replace(dest + ".part", dest)
            self.wasm_state = "ready"
            log.info("ffmpeg.wasm core cached in %s", self.settings.vendor_path)
        except Exception as exc:
            self.wasm_state = "error"
            log.warning("Couldn't fetch ffmpeg.wasm core (%s); browsers will fall back to the CDN.", exc)

    def ffmpeg_core_urls(self) -> dict[str, str]:
        if self.settings.wasm_source == "local" and self.wasm_state == "ready":
            return {"js": "vendor/ffmpeg-core.js", "wasm": "vendor/ffmpeg-core.wasm", "source": "local"}
        return {"js": FFMPEG_CDN + "ffmpeg-core.js", "wasm": FFMPEG_CDN + "ffmpeg-core.wasm", "source": "cdn"}


def _secret_key(settings: Settings) -> bytes:
    path = os.path.join(settings.data_path, "secret.key")
    try:
        with open(path, "rb") as fh:
            key = fh.read()
            if len(key) >= 32:
                return key
    except OSError:
        pass
    key = secrets.token_bytes(48)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
    except OSError:
        pass
    return key


def _err(message: str, status: int = 400, **extra: Any) -> tuple[Response, int]:
    return jsonify({"error": message, **extra}), status


def _short(path: str | None) -> str | None:
    """Show paths inside the app folder relative to it (shorter, and no home-dir names)."""
    if not path:
        return path
    try:
        if os.path.commonpath([APP_DIR, os.path.abspath(path)]) == APP_DIR:
            return os.path.relpath(path, APP_DIR)
    except ValueError:  # different drives on Windows
        pass
    return path


def _attachment(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def create_app(settings: Settings, start_background: bool = True) -> Flask:
    settings.ensure_dirs()
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    app.secret_key = _secret_key(settings)
    app.config.update(
        SESSION_COOKIE_NAME="ytdlui_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,
        MAX_CONTENT_LENGTH=256 * 1024,
        SEND_FILE_MAX_AGE_DEFAULT=0,  # revalidate (ETag) so updates show up immediately
    )
    app.json.sort_keys = False  # type: ignore[attr-defined]

    tp = str(settings.trust_proxy).strip().lower()
    if tp.isdigit() and int(tp) > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        n = int(tp)
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n, x_prefix=n)  # type: ignore[method-assign]

    svc = Services(settings)
    app.extensions["ytdlui"] = svc
    if start_background:
        svc.start_background()

    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        index_html = fh.read().replace("__VERSION__", __version__)
    with open(os.path.join(STATIC_DIR, "login.html"), encoding="utf-8") as fh:
        login_html = fh.read().replace("__VERSION__", __version__)

    # ── helpers ─────────────────────────────────────────────────────────
    def throttled(resp: Response) -> Response:
        """Pace a file response through the server-wide bandwidth cap."""
        if svc.bandwidth.enabled and resp.status_code in (200, 206) and request.method != "HEAD":
            resp.response = svc.bandwidth.wrap(resp.response)
            resp.direct_passthrough = True
        return resp

    def client_ip() -> str:
        if tp == "cloudflare":
            return request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"
        return request.remote_addr or "?"

    def limited(bucket: str, limit: int) -> tuple[Response, int] | None:
        ip = client_ip()
        if not svc.limiter.hit(bucket, ip, limit):
            resp, code = _err("Too many requests — slow down a little.", 429)
            resp.headers["Retry-After"] = str(svc.limiter.retry_after(bucket, ip))
            return resp, code
        return None

    def body() -> dict[str, Any]:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def validated_url(raw: str) -> str:
        return check_url(raw, allowed_sites=settings.allowed_sites,
                         allow_private=settings.allow_private_urls)

    def admin_session() -> bool:
        """Logged in as admin (setup may still be pending). Old sessions die on password change."""
        return (svc.admin is not None and session.get("admin") is True
                and session.get("admin_ver") == svc.admin.version)

    def is_admin() -> bool:
        return admin_session() and svc.admin.setup_step() == "done"

    def admin_only(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*a: Any, **kw: Any) -> Any:
            if svc.admin is None:
                abort(404)
            if not admin_session():
                return _err("Admin login required.", 401)
            step = svc.admin.setup_step()
            if step != "done":
                return _err("Finish the admin setup first.", 403, setup=step)
            return fn(*a, **kw)
        return wrapper

    def locked_response(scope: str) -> tuple[Response, int] | None:
        wait = svc.guard.locked_for(scope, client_ip())
        if not wait:
            return None
        mins = (wait + 59) // 60
        resp, code = _err(f"Too many failed attempts. Try again in {mins} minute{'s' if mins != 1 else ''}.",
                          429, locked=wait)
        resp.headers["Retry-After"] = str(wait)
        return resp, code

    def failed_login(scope: str, message: str) -> tuple[Response, int]:
        ip = client_ip()
        lock = svc.guard.failure(scope, ip)
        if lock:
            return locked_response(scope) or _err(message, 403)
        left = svc.guard.remaining(scope, ip)
        hint = f" {left} attempt{'s' if left != 1 else ''} left before a lockout." if left <= 3 else ""
        return _err(message + hint, 403)

    # ── auth gate + headers ─────────────────────────────────────────────
    @app.before_request
    def gate() -> Any:
        if not settings.password or session.get("auth") is True or admin_session():
            return None
        path = request.path
        if any(path.startswith(p) for p in OPEN_PATHS):
            return None
        if path.startswith("/api/"):
            return _err("Password required.", 401, login=True)
        return Response(login_html, mimetype="text/html", headers={"Cache-Control": "no-store"})

    @app.after_request
    def headers(resp: Response) -> Response:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https: data: blob:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'; "
            "worker-src 'self' blob:; connect-src 'self' https: blob:; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'; object-src 'none'")
        resp.headers.setdefault("Server", "YTDL-UI")
        return resp

    # ── pages ───────────────────────────────────────────────────────────
    @app.get("/")
    def index() -> Response:
        return Response(index_html, mimetype="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest() -> Response:
        return send_from_directory(STATIC_DIR, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return send_from_directory(os.path.join(STATIC_DIR, "img"), "icon.svg", mimetype="image/svg+xml")

    @app.get("/vendor/<name>")
    def vendor(name: str) -> Any:
        if name not in FFMPEG_CORE_FILES or svc.wasm_state != "ready":
            abort(404)
        mime = "application/wasm" if name.endswith(".wasm") else "text/javascript"
        return throttled(send_from_directory(settings.vendor_path, name, mimetype=mime, max_age=30 * 24 * 3600))

    # ── auth ────────────────────────────────────────────────────────────
    @app.post("/api/login")
    def login() -> Any:
        if (r := limited("login", 30)) or (r := locked_response("site")):
            return r
        if not settings.password:
            return jsonify({"ok": True})
        if safe_equals(str(body().get("password") or ""), settings.password):
            svc.guard.success("site", client_ip())
            session.permanent = True
            session["auth"] = True
            return jsonify({"ok": True})
        return failed_login("site", "Wrong password.")

    @app.post("/api/logout")
    def logout() -> Any:
        session.clear()
        return jsonify({"ok": True})

    # ── public API ──────────────────────────────────────────────────────
    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"ok": True, "version": __version__, "ytdlp": svc.ytdlp.available})

    @app.get("/api/config")
    def config() -> Any:
        counts = svc.jobs.counts()
        data: dict[str, Any] = {
            "version": __version__,
            "ytdlp_version": svc.ytdlp.version(),
            "ytdlp_ok": svc.ytdlp.available,
            "ffmpeg_ok": bool(svc.ytdlp.tools.ffmpeg),
            "processing": {
                "mode": settings.processing_mode,
                "default": settings.processing_default,
                "fallback": settings.fallback_to_server,
                "relay": settings.relay,
                "browser_max_mb": settings.browser_max_mb,
                "ffmpeg_core": svc.ffmpeg_core_urls(),
            },
            "server_downloads": settings.server_downloads_enabled,
            "ui": {"accent": settings.ui_accent, "style": settings.ui_style},
            "formats": {
                "video": [{"id": k, **v} for k, v in VIDEO_FORMATS.items()],
                "audio": [{"id": k, **v} for k, v in AUDIO_FORMATS.items()],
            },
            "bitrates": AUDIO_BITRATES,
            "default_bitrate": DEFAULT_BITRATE,
            "limits": {
                "max_playlist_items": settings.max_playlist_items,
                "max_duration_minutes": settings.max_duration_minutes,
                "max_filesize_mb": settings.max_filesize_mb,
                "job_ttl_minutes": settings.job_ttl_minutes,
                "allowed_sites": settings.allowed_sites,
            },
            "load": {**counts, "max_concurrent": settings.max_concurrent_jobs, "busy": svc.jobs.busy()},
            "auth": {"password": bool(settings.password), "admin_enabled": svc.admin is not None,
                     "admin": is_admin(),
                     "admin_setup": svc.admin.setup_step() if admin_session() else None},
        }
        if is_admin():
            data["update"] = svc.app_updater.status()
            data["ytdlp_update"] = svc.ytdlp_updater.status()
        return jsonify(data)

    @app.post("/api/info")
    def info() -> Any:
        if (r := limited("info", settings.rate_limit)):
            return r
        data = body()
        try:
            url = validated_url(str(data.get("url") or ""))
        except URLRejected as exc:
            return _err(str(exc))
        if not svc.ytdlp.available:
            return _err("yt-dlp isn't installed on the server yet. Try again in a minute.", 503)
        try:
            return jsonify(svc.ytdlp.info(url, no_playlist=bool(data.get("no_playlist"))))
        except YtDlpError as exc:
            return _err(str(exc), 422)

    # ── server-side jobs ────────────────────────────────────────────────
    def make_request(data: dict[str, Any]) -> Request:
        req = Request.from_json(data, settings.max_playlist_items)
        req.url = validated_url(req.url)
        return req

    @app.post("/api/jobs")
    def create_job() -> Any:
        if not settings.server_downloads_enabled:
            return _err("This server only processes downloads in your browser.", 403)
        if (r := limited("download", settings.download_rate_limit)):
            return r
        data = body()
        try:
            req = make_request(data)
        except (URLRejected, ValueError) as exc:
            return _err(str(exc))
        if not svc.ytdlp.available:
            return _err("yt-dlp isn't installed on the server yet. Try again in a minute.", 503)
        try:
            job = svc.jobs.submit(req, client_ip(), title=str(data.get("title") or "")[:300])
        except JobRejected as exc:
            return _err(str(exc), exc.status)
        return jsonify(svc.jobs.public(job)), 201

    @app.get("/api/jobs")
    def list_jobs() -> Any:
        ids = [i for i in (request.args.get("ids") or "").split(",") if i][:50]
        out = []
        for i in ids:
            job = svc.jobs.get(i)
            if job:
                out.append(svc.jobs.public(job))
        return jsonify({"jobs": out, "load": {**svc.jobs.counts(), "busy": svc.jobs.busy()}})

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str) -> Any:
        job = svc.jobs.get(job_id)
        if not job:
            return _err("Download not found (it may have expired).", 404)
        return jsonify(svc.jobs.public(job))

    @app.delete("/api/jobs/<job_id>")
    def delete_job(job_id: str) -> Any:
        if not svc.jobs.remove(job_id):
            return _err("Download not found.", 404)
        return jsonify({"ok": True})

    def _send_job_file(job_id: str, index: int | None) -> Any:
        job = svc.jobs.get(job_id)
        if not job or job.status != "done" or not job.files:
            return _err("That file isn't ready or has expired.", 404)
        if index is None and len(job.files) == 1:
            index = 0
        if index is not None:
            if not 0 <= index < len(job.files):
                return _err("No such file.", 404)
            f = job.files[index]
            ext = f["name"].rsplit(".", 1)[-1].lower()
            return throttled(send_file(f["path"], mimetype=MIME_TYPES.get(ext, "application/octet-stream"),
                                       as_attachment=True, download_name=f["name"], conditional=True, max_age=0))
        name = safe_filename(job.title or "playlist", "zip")
        files = [(f["path"], f["name"]) for f in job.files]
        body_iter = svc.bandwidth.wrap(stream_zip(files)) if svc.bandwidth.enabled else stream_zip(files)
        return Response(body_iter, mimetype="application/zip",
                        headers={"Content-Disposition": _attachment(name), "Cache-Control": "no-store"},
                        direct_passthrough=True)

    @app.get("/api/jobs/<job_id>/file")
    def job_file(job_id: str) -> Any:
        return _send_job_file(job_id, None)

    @app.get("/api/jobs/<job_id>/files/<int:index>")
    def job_file_n(job_id: str, index: int) -> Any:
        return _send_job_file(job_id, index)

    # Legacy one-shot endpoints (v1 API) — handy for curl/scripts.
    def _legacy(playlist: bool) -> Any:
        if not settings.server_downloads_enabled:
            return _err("This server only processes downloads in your browser.", 403)
        if (r := limited("download", settings.download_rate_limit)):
            return r
        data = dict(body())
        data["playlist"] = playlist
        try:
            req = make_request(data)
            job = svc.jobs.submit(req, client_ip())
        except (URLRejected, ValueError) as exc:
            return _err(str(exc))
        except JobRejected as exc:
            return _err(str(exc), exc.status)
        while job.status in ("queued", "running"):
            time.sleep(0.5)
        if job.status != "done":
            return _err(job.error or "Download failed.", 500)
        return _send_job_file(job.id, None)

    @app.post("/api/download")
    def legacy_download() -> Any:
        return _legacy(False)

    @app.post("/api/download-playlist")
    def legacy_playlist() -> Any:
        return _legacy(True)

    # ── browser-side processing ─────────────────────────────────────────
    @app.post("/api/resolve")
    def resolve_route() -> Any:
        if not settings.browser_processing_enabled:
            return _err("Browser processing is disabled on this server.", 403)
        if (r := limited("resolve", max(60, settings.rate_limit * 4))):
            return r
        try:
            req = make_request(body())
        except (URLRejected, ValueError) as exc:
            return _err(str(exc))
        if not svc.ytdlp.available:
            return _err("yt-dlp isn't installed on the server yet. Try again in a minute.", 503)
        try:
            plan = resolve(svc.ytdlp, svc.tokens, settings, req)
        except FallbackNeeded as exc:
            return _err(str(exc), 422, fallback=True)
        except YtDlpError as exc:
            return _err(str(exc), 422)
        if plan["total_size"] and plan["total_size"] > settings.browser_max_mb * 1024 * 1024:
            return _err(f"This file is bigger than {settings.browser_max_mb} MB, too large to process "
                        "in a browser.", 422, fallback=True)
        return jsonify(plan)

    @app.get("/api/relay/<token>")
    def relay_route(token: str) -> Any:
        try:
            status, hdrs, stream = svc.relay.open(token, request.headers.get("Range"))
        except RelayError as exc:
            return _err(str(exc), exc.status)
        return Response(stream, status=status, headers=hdrs, direct_passthrough=True)

    # ── admin ───────────────────────────────────────────────────────────
    @app.post("/api/admin/login")
    def admin_login() -> Any:
        if svc.admin is None:
            abort(404)
        if (r := limited("admin-login", 30)) or (r := locked_response("admin")):
            return r
        data = body()
        if not svc.admin.check_password(str(data.get("password") or "")):
            log.warning("Failed admin login from %s", client_ip())
            return failed_login("admin", "Wrong password or code.")
        if svc.admin.totp_enabled:
            code = str(data.get("code") or "").strip()
            if not code:
                return _err("Enter the 6-digit code from your authenticator app.", 401, need_code=True)
            if not svc.admin.check_second_factor(code):
                log.warning("Failed admin 2FA from %s", client_ip())
                return failed_login("admin", "Wrong password or code.")
        svc.guard.success("admin", client_ip())
        session.permanent = True
        session["admin"] = True
        session["admin_ver"] = svc.admin.version
        if settings.password:
            session["auth"] = True
        log.info("Admin logged in from %s", client_ip())
        return jsonify({"ok": True, "setup": svc.admin.setup_step()})

    @app.post("/api/admin/logout")
    def admin_logout() -> Any:
        session.pop("admin", None)
        session.pop("admin_ver", None)
        return jsonify({"ok": True})

    def setup_session() -> tuple[Response, int] | None:
        if svc.admin is None:
            abort(404)
        if not admin_session():
            return _err("Admin login required.", 401)
        return None

    @app.post("/api/admin/totp/begin")
    def totp_begin() -> Any:
        if (r := setup_session()):
            return r
        if svc.admin.totp_enabled:
            return _err("Two-factor authentication is already set up.", 409)
        secret = svc.admin.begin_totp()
        host = (request.host or "server").split(":")[0]
        uri = totp_uri(secret, f"admin@{host}")
        return jsonify({"secret": secret, "uri": uri, "qr": qr_svg_data_uri(uri)})

    @app.post("/api/admin/totp/confirm")
    def totp_confirm() -> Any:
        if (r := setup_session()) or (r := locked_response("admin")):
            return r
        codes = svc.admin.confirm_totp(str(body().get("code") or ""))
        if codes is None:
            return failed_login("admin", "That code didn't match. Check your phone's clock and try the newest code.")
        session["admin_ver"] = svc.admin.version
        log.info("Admin enabled TOTP 2FA")
        return jsonify({"ok": True, "recovery_codes": codes, "setup": svc.admin.setup_step()})

    @app.post("/api/admin/password")
    def admin_password() -> Any:
        """Change the admin password — needs the current password and a 2FA code."""
        if (r := setup_session()) or (r := locked_response("admin")):
            return r
        if not svc.admin.totp_enabled:
            return _err("Set up two-factor authentication first.", 403, setup="totp")
        data = body()
        current, new = str(data.get("current") or ""), str(data.get("new") or "")
        if (problem := password_problem(new, current)):
            return _err(problem)                  # checked first so a typo doesn't burn the 2FA code
        if not svc.admin.check_password(current) or not svc.admin.check_second_factor(str(data.get("code") or "")):
            return failed_login("admin", "Current password or code is wrong.")
        svc.admin.set_password(new)
        session["admin_ver"] = svc.admin.version     # keep this session, sign out all others
        log.warning("Admin password changed from %s", client_ip())
        return jsonify({"ok": True, "message": "Password changed. Other admin sessions were signed out."})

    @app.post("/api/admin/recovery-codes")
    @admin_only
    def admin_recovery() -> Any:
        if (r := locked_response("admin")):
            return r
        data = body()
        if not svc.admin.check_password(str(data.get("current") or "")) or \
                not svc.admin.check_second_factor(str(data.get("code") or "")):
            return failed_login("admin", "Current password or code is wrong.")
        return jsonify({"ok": True, "recovery_codes": svc.admin.regenerate_recovery()})

    def apply_live(name: str) -> None:
        if name == "bandwidth_limit":
            svc.bandwidth.set_limit(settings.bandwidth_limit)
        elif name == "login_max_attempts":
            svc.guard.max_attempts = max(1, settings.login_max_attempts)
        elif name == "login_lockout_minutes":
            svc.guard.base = max(1.0, settings.login_lockout_minutes) * 60

    def setting_view(name: str) -> dict[str, Any]:
        f = FIELD_MAP[name]
        live, sensitive = ADMIN_EDITABLE[name]
        value = getattr(settings, name)
        view = {"name": name, "section": f.section, "type": f.type.__name__, "help": f.help,
                "choices": list(f.choices) if f.choices else None, "source": settings.sources.get(name, "default"),
                "locked": settings.sources.get(name) in ("env", "cli"), "restart": not live, "sensitive": sensitive,
                "default": f.default}
        if name == "password":
            view.update(value="", is_set=bool(value), default="")
        else:
            view["value"] = value
        return view

    @app.get("/api/admin/settings")
    @admin_only
    def admin_settings() -> Any:
        return jsonify({"settings": [setting_view(n) for n in ADMIN_EDITABLE]})

    @app.post("/api/admin/settings")
    @admin_only
    def admin_settings_save() -> Any:
        if (r := locked_response("admin")):
            return r
        data = body()
        changes = data.get("changes")
        if not isinstance(changes, dict) or not changes:
            return _err("Nothing to save.")
        parsed: dict[str, Any] = {}
        for name, raw in changes.items():
            if name not in ADMIN_EDITABLE:
                return _err(f"'{name}' can't be changed from the admin panel.")
            if settings.sources.get(name) in ("env", "cli"):
                return _err(f"'{name}' is set by an environment variable or command-line flag on the server.")
            try:
                parsed[name] = None if raw is None else coerce_setting(name, raw)
            except ConfigError as exc:
                return _err(str(exc))
            if name == "ui_accent" and parsed[name] is not None:
                import re as _re
                if not _re.fullmatch(r"#[0-9a-fA-F]{6}", parsed[name]):
                    return _err("ui_accent must be a hex color like #22d3ee.")
                parsed[name] = parsed[name].lower()
        if any(ADMIN_EDITABLE[n][1] for n in parsed):
            if not svc.admin.check_second_factor(str(data.get("code") or "")):
                return failed_login("admin", "Security settings need a current 2FA code.")
        overrides = read_admin_overrides(settings.data_path)
        restart = []
        for name, value in parsed.items():
            if value is None:                                  # reset to config.ini / default
                overrides.pop(name, None)
                base = getattr(settings, "base_values", {})
                value = base.get(name, FIELD_MAP[name].default)
                settings.sources[name] = "config" if name in base else "default"
            else:
                overrides[name] = value
                settings.sources[name] = "admin"
            setattr(settings, name, value)
            if ADMIN_EDITABLE[name][0]:
                apply_live(name)
            else:
                restart.append(name)
        write_admin_overrides(settings.data_path, overrides)
        log.warning("Admin changed settings from %s: %s", client_ip(), ", ".join(sorted(parsed)))
        msg = "Saved." + (f" Restart the server for {', '.join(restart)} to take effect." if restart else "")
        return jsonify({"ok": True, "message": msg, "restart": restart,
                        "settings": [setting_view(n) for n in ADMIN_EDITABLE]})

    @app.get("/api/admin/lockouts")
    @admin_only
    def admin_lockouts() -> Any:
        return jsonify({"lockouts": svc.guard.lockouts()})

    @app.post("/api/admin/unlock")
    @admin_only
    def admin_unlock() -> Any:
        data = body()
        return jsonify({"ok": svc.guard.unlock(str(data.get("scope") or ""), str(data.get("ip") or ""))})

    @app.get("/api/admin/status")
    @admin_only
    def admin_status() -> Any:
        try:
            disk = shutil.disk_usage(settings.tmp_path)
            disk_info = {"free": disk.free, "total": disk.total}
        except OSError:
            disk_info = None
        t = svc.ytdlp.tools
        return jsonify({
            "app": svc.app_updater.status(),
            "ytdlp": svc.ytdlp_updater.status(),
            "ffmpeg": {"path": t.ffmpeg, "version": svc.ytdlp.ffmpeg_version(), "install": svc.ffmpeg_install,
                       "installable": bool(ffmpeg_asset())},
            "js_runtime": {"name": t.js_runtime, "path": t.js_runtime_path},
            "jobs": svc.jobs.counts(),
            "tmp_bytes": svc.jobs.tmp_usage(),
            "disk": disk_info,
            "relay": {"bytes_total": svc.relay.bytes_total, "active": svc.relay.active, "tokens": len(svc.tokens)},
            "bandwidth": {
                "limit_mb": settings.bandwidth_limit,
                "current": svc.bandwidth.current_rate(),
                "served_total": svc.bandwidth.bytes_total,
                "per_download_mb": (ytdlp_rate(settings.bandwidth_limit, settings.max_concurrent_jobs) or 0) / MB,
            },
            "processing": {"mode": settings.processing_mode, "default": settings.processing_default,
                           "fallback": settings.fallback_to_server, "relay": settings.relay,
                           "wasm": svc.ffmpeg_core_urls()["source"]},
            "uptime": time.time() - svc.started,
            "security": {"totp": svc.admin.totp_enabled, "recovery_left": svc.admin.recovery_left(),
                         "lockouts": svc.guard.lockouts(), "max_attempts": settings.login_max_attempts,
                         "lockout_minutes": settings.login_lockout_minutes},
            "config_path": _short(settings.config_path),
            "data_path": _short(settings.data_path),
        })

    @app.get("/api/admin/jobs")
    @admin_only
    def admin_jobs() -> Any:
        out = []
        for j in svc.jobs.all_jobs()[:200]:
            d = svc.jobs.public(j)
            d["client"] = j.owner
            d["host"] = (j.req.url.split("/")[2] if "://" in j.req.url else "")
            out.append(d)
        return jsonify({"jobs": out})

    @app.post("/api/admin/jobs/<job_id>/cancel")
    @admin_only
    def admin_cancel(job_id: str) -> Any:
        return jsonify({"ok": svc.jobs.cancel(job_id)})

    @app.post("/api/admin/check-updates")
    @admin_only
    def admin_check() -> Any:
        return jsonify({"app": svc.app_updater.check(), "ytdlp": svc.ytdlp_updater.check()})

    @app.post("/api/admin/update-ytdlp")
    @admin_only
    def admin_update_ytdlp() -> Any:
        if not svc.jobs.idle() and not body().get("force"):
            return _err("Downloads are running. Wait for them to finish or force the update.", 409)
        return jsonify(svc.ytdlp_updater.update(force=bool(body().get("force"))))

    @app.post("/api/admin/update-app")
    @admin_only
    def admin_update_app() -> Any:
        if not settings.allow_web_update:
            return _err("Web updates are disabled. Set allow_web_update = true, or run the update "
                        "script on the server.", 403)
        if not svc.jobs.idle() and not body().get("force"):
            return _err("Downloads are running. Wait for them to finish or force the update.", 409)
        result = svc.app_updater.apply()
        if result.get("ok") and result.get("restart"):
            restart_process(1.5, before=svc.jobs.shutdown)
        return jsonify(result)

    @app.post("/api/admin/install-ffmpeg")
    @admin_only
    def admin_install_ffmpeg() -> Any:
        threading.Thread(target=svc.install_ffmpeg, daemon=True).start()
        return jsonify({"ok": True, "message": "Installing ffmpeg in the background."})

    @app.post("/api/admin/cleanup")
    @admin_only
    def admin_cleanup() -> Any:
        return jsonify({"removed": svc.jobs.purge_finished()})

    @app.post("/api/admin/restart")
    @admin_only
    def admin_restart() -> Any:
        restart_process(1.0, before=svc.jobs.shutdown)
        return jsonify({"ok": True})

    @app.errorhandler(404)
    def not_found(_e: Any) -> Any:
        if request.path.startswith("/api/"):
            return _err("Not found.", 404)
        return Response("Not found", status=404, mimetype="text/plain")

    @app.errorhandler(413)
    def too_big(_e: Any) -> Any:
        return _err("Request too large.", 413)

    @app.errorhandler(500)
    def server_error(_e: Any) -> Any:
        return _err("Something went wrong on the server.", 500)

    return app
