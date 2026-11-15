"""Settings: built-in defaults < config.ini < YTDLUI_* environment variables < CLI flags.

Every setting has one flat name (e.g. ``processing_mode``). In ``config.ini`` it can
live in any section — sections only exist to keep the file readable. As an
environment variable it is upper-cased and prefixed: ``YTDLUI_PROCESSING_MODE``.
"""

from __future__ import annotations

import configparser
import os
import platform
import sys
from dataclasses import dataclass
from typing import Any

FROZEN = bool(getattr(sys, "frozen", False))
# Standalone executables keep config.ini and data/ next to the executable.
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
           else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def user_data_dir() -> str:
    """Per-user fallback location when the app folder isn't writable."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "YTDL-UI")
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/YTDL-UI")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "ytdl-ui")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class Field:
    name: str
    section: str
    type: type
    default: Any
    help: str
    choices: tuple[str, ...] | None = None


FIELDS: tuple[Field, ...] = (
    # ── server ────────────────────────────────────────────────────────────
    Field("host", "server", str, "127.0.0.1",
          "Address to bind. 127.0.0.1 = this machine only (use with a reverse proxy/tunnel); "
          "0.0.0.0 = every network interface."),
    Field("port", "server", int, 5000, "Port to listen on."),
    Field("threads", "server", int, 16,
          "Worker threads for the HTTP server. Each in-flight download or relay holds one."),
    Field("trust_proxy", "server", str, "0",
          "How many reverse proxies sit in front of the app (0 = none). Set to 1 behind "
          "Caddy/Nginx/cloudflared so rate limits see real client IPs, or 'cloudflare' to "
          "trust the CF-Connecting-IP header."),
    Field("data_dir", "server", str, "data",
          "Where temp downloads, caches and update state live (relative to the app folder)."),
    Field("log_urls", "server", bool, False,
          "Log the full URL of every download. Off by default: only the site is logged."),

    # ── security ──────────────────────────────────────────────────────────
    Field("password", "security", str, "",
          "Optional password visitors must enter before using the site."),
    Field("admin_enabled", "security", bool, True,
          "Enable the admin panel (⚙): updates, job overview, cleanup, password and 2FA settings."),
    Field("admin_password", "security", str, "",
          "Initial admin password, used only until the admin replaces it (the first login forces "
          "a TOTP 2FA setup and a password change; afterwards credentials live in data/admin.json). "
          "Empty = generate a random one on first start and print it to the log and "
          "data/initial-admin-password.txt. Lost access? Run with --reset-admin."),
    Field("login_max_attempts", "security", int, 5,
          "Failed password/2FA attempts allowed per IP before that IP is locked out."),
    Field("login_lockout_minutes", "security", int, 15,
          "First lockout length. Each repeat lockout of the same IP doubles it (max 24 h)."),
    Field("allowed_sites", "security", list, [],
          "Comma-separated domains users may download from (e.g. youtube.com, youtu.be, "
          "soundcloud.com). Empty = anything yt-dlp supports."),
    Field("allow_private_urls", "security", bool, False,
          "Allow URLs that resolve to private/LAN/loopback addresses. Keep off on public servers."),
    Field("rate_limit", "security", int, 30,
          "Metadata lookups per minute per client IP."),
    Field("download_rate_limit", "security", int, 10,
          "New downloads per minute per client IP."),

    # ── limits ────────────────────────────────────────────────────────────
    Field("max_concurrent_jobs", "limits", int, 2,
          "Server-side downloads that run at the same time. Extra jobs wait in a queue."),
    Field("max_jobs_per_client", "limits", int, 3,
          "Queued + running server downloads allowed per client IP."),
    Field("max_queue", "limits", int, 25, "Maximum queued server downloads overall."),
    Field("max_duration_minutes", "limits", int, 0,
          "Reject videos longer than this (0 = no limit)."),
    Field("max_filesize_mb", "limits", int, 0,
          "Reject downloads larger than this (0 = no limit)."),
    Field("max_playlist_items", "limits", int, 100,
          "Most playlist entries one request may download."),
    Field("job_ttl_minutes", "limits", int, 30,
          "How long finished files stay on the server for the user to save them."),
    Field("job_timeout_minutes", "limits", int, 120,
          "Kill a server download that runs longer than this."),
    Field("min_free_space_mb", "limits", int, 1024,
          "Refuse new server downloads when free disk space drops below this."),
    Field("bandwidth_limit", "limits", float, 10.0,
          "Cap, in MB/s, on the media traffic this server handles for users, so downloads can't "
          "starve its network connection. Finished files, playlist ZIPs and relayed browser-mode "
          "streams share it; server-side yt-dlp downloads are capped at the same total (split "
          "evenly across max_concurrent_jobs). Decimals are fine (e.g. 2.5). 0 = unlimited."),

    # ── processing ────────────────────────────────────────────────────────
    Field("processing_mode", "processing", str, "choice",
          "Where downloads are processed. server = yt-dlp + ffmpeg on this machine. "
          "browser = the visitor's device does the work with ffmpeg.wasm and the server "
          "only looks up stream URLs (and relays bytes when a site requires it). "
          "choice = visitors can switch between the two.",
          ("server", "browser", "choice")),
    Field("processing_default", "processing", str, "server",
          "Initial choice when processing_mode = choice. auto = use the browser when the "
          "device looks capable (not on a metered/slow connection), otherwise the server.",
          ("server", "browser", "auto")),
    Field("fallback_to_server", "processing", bool, True,
          "When browser processing can't handle a link (HLS-only sites, too big, very old "
          "browser), fall back to server processing instead of failing. Ignored when "
          "processing_mode = browser and this is false — then the server never downloads."),
    Field("relay", "processing", bool, True,
          "Let the server stream media bytes to the browser when the source blocks direct "
          "browser access (YouTube always does: its URLs are locked to the server's IP). "
          "Relayed bytes are never written to disk and never touch ffmpeg."),
    Field("browser_max_mb", "processing", int, 1024,
          "Largest download (MB) the browser is asked to process in memory."),
    Field("wasm_source", "processing", str, "cdn",
          "Where browsers load the ~31 MB ffmpeg.wasm core from. cdn = jsDelivr (no load on "
          "your server). local = downloaded once by the server (SHA-256 verified) and served "
          "from here.",
          ("cdn", "local")),

    # ── yt-dlp ────────────────────────────────────────────────────────────
    Field("ytdlp_path", "ytdlp", str, "",
          "Path to a yt-dlp executable. Empty = use the yt-dlp Python package if installed, "
          "else yt-dlp(.exe) in the app folder, else yt-dlp on PATH."),
    Field("ffmpeg_path", "ytdlp", str, "",
          "Path to ffmpeg. Empty = ffmpeg(.exe) in the app folder, else PATH."),
    Field("js_runtime", "ytdlp", str, "auto",
          "JavaScript runtime yt-dlp uses for YouTube's player challenges. auto = deno, "
          "then node, then bun.",
          ("auto", "deno", "node", "bun", "none")),
    Field("cookies_file", "ytdlp", str, "",
          "Netscape-format cookies.txt passed to yt-dlp (helps with 'confirm you're not a "
          "bot' and age-restricted videos). Use a throwaway account."),
    Field("proxy", "ytdlp", str, "",
          "HTTP(S) proxy for yt-dlp and the relay, e.g. http://user:pass@host:3128."),
    Field("extra_args", "ytdlp", str, "",
          "Extra yt-dlp arguments appended to every call (shell-style quoting)."),

    # ── updates ───────────────────────────────────────────────────────────
    Field("update_check", "updates", bool, True,
          "Check GitHub for new YTDL-UI releases (shown to admins and in the server log)."),
    Field("update_repo", "updates", str, "JustAyden/YTDL-UI", "GitHub repo to check."),
    Field("update_check_hours", "updates", int, 12, "Hours between release checks."),
    Field("allow_web_update", "updates", bool, False,
          "Let admins install YTDL-UI updates from the admin panel (the app restarts itself)."),
    Field("ytdlp_auto_update", "updates", bool, True,
          "Keep yt-dlp up to date automatically. Sites change constantly; an old yt-dlp is "
          "the #1 cause of failed downloads."),
    Field("ytdlp_update_hours", "updates", int, 24, "Hours between yt-dlp update checks."),
    Field("ytdlp_channel", "updates", str, "stable", "yt-dlp release channel.",
          ("stable", "nightly")),
    Field("ffmpeg_auto_install", "updates", bool, True,
          "If ffmpeg is missing on Windows or Linux, download a static build (from "
          "yt-dlp/FFmpeg-Builds, checksum-verified) into data/bin on first start. Not needed "
          "when processing_mode = browser."),
)

FIELD_MAP = {f.name: f for f in FIELDS}


class ConfigError(ValueError):
    pass


def _coerce(field: Field, raw: Any) -> Any:
    if field.type is bool:
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
        raise ConfigError(f"{field.name}: expected true/false, got {raw!r}")
    if field.type is int:
        try:
            return int(str(raw).strip())
        except ValueError:
            raise ConfigError(f"{field.name}: expected a whole number, got {raw!r}") from None
    if field.type is float:
        try:
            value = float(str(raw).strip())
        except ValueError:
            raise ConfigError(f"{field.name}: expected a number, got {raw!r}") from None
        if value < 0 or value != value or value == float("inf"):
            raise ConfigError(f"{field.name}: must be 0 or a positive number, got {raw!r}")
        return value
    if field.type is list:
        if isinstance(raw, (list, tuple)):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [p.strip() for p in str(raw).split(",") if p.strip()]
    value = str(raw).strip()
    if field.choices and value.lower() not in field.choices:
        raise ConfigError(f"{field.name}: must be one of {', '.join(field.choices)}; got {raw!r}")
    return value.lower() if field.choices else value


class Settings:
    """Plain attribute bag of resolved settings plus a few derived paths."""

    def __init__(self, values: dict[str, Any], config_path: str | None = None):
        for f in FIELDS:
            setattr(self, f.name, values.get(f.name, f.default))
        self.config_path = config_path
        self.app_dir = APP_DIR
        if os.path.isabs(self.data_dir):
            data = self.data_dir
        else:
            data = os.path.join(APP_DIR, self.data_dir)
            if not os.path.isdir(data) and not os.access(APP_DIR, os.W_OK):
                data = os.path.join(user_data_dir(), self.data_dir)
        self.data_path = os.path.abspath(data)
        self.tmp_path = os.path.join(self.data_path, "tmp")
        self.cache_path = os.path.join(self.data_path, "cache")
        self.vendor_path = os.path.join(self.data_path, "vendor")

    # convenience ---------------------------------------------------------
    @property
    def server_downloads_enabled(self) -> bool:
        if self.processing_mode == "browser" and not self.fallback_to_server:
            return False
        return True

    @property
    def browser_processing_enabled(self) -> bool:
        return self.processing_mode in ("browser", "choice")

    def ensure_dirs(self) -> None:
        for p in (self.data_path, self.tmp_path, self.cache_path, self.vendor_path):
            os.makedirs(p, exist_ok=True)

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in FIELDS}


def default_config_path() -> str:
    return os.environ.get("YTDLUI_CONFIG") or os.path.join(APP_DIR, "config.ini")


def load_settings(config_path: str | None = None, overrides: dict[str, Any] | None = None,
                  environ: dict[str, str] | None = None) -> Settings:
    environ = os.environ if environ is None else environ
    values: dict[str, Any] = {}

    path = config_path or default_config_path()
    used_path = None
    if path and os.path.isfile(path):
        cp = configparser.ConfigParser(interpolation=None)
        with open(path, encoding="utf-8") as fh:
            cp.read_file(fh)
        for section in cp.sections():
            for key, raw in cp.items(section):
                field = FIELD_MAP.get(key)
                if field is None:
                    raise ConfigError(f"{os.path.basename(path)}: unknown setting '{key}' in [{section}]")
                values[key] = _coerce(field, raw)
        used_path = path
    elif config_path:
        raise ConfigError(f"Config file not found: {config_path}")

    for f in FIELDS:
        env_key = f"YTDLUI_{f.name.upper()}"
        if env_key in environ:
            values[f.name] = _coerce(f, environ[env_key])

    for key, raw in (overrides or {}).items():
        field = FIELD_MAP.get(key)
        if field is None:
            raise ConfigError(f"Unknown setting '{key}'")
        if raw is not None:
            values[key] = _coerce(field, raw)

    return Settings(values, used_path)


def render_example() -> str:
    """Generate config.example.ini from the field registry (keeps docs in sync)."""
    import textwrap

    out = [
        "# YTDL-UI configuration",
        "# Copy to config.ini and uncomment what you want to change.",
        "# Any setting can also be set with an environment variable: YTDLUI_<NAME>",
        "# (e.g. YTDLUI_PROCESSING_MODE=browser) or on the command line with",
        "# --set name=value. Precedence: CLI > environment > config.ini > defaults.",
        "",
    ]
    section = None
    for f in FIELDS:
        if f.section != section:
            section = f.section
            out += ["", f"[{section}]"]
        out.append("")
        for line in textwrap.wrap(f.help, 86):
            out.append(f"# {line}")
        if f.choices:
            out.append(f"# One of: {', '.join(f.choices)}")
        default = f.default
        if isinstance(default, float) and default.is_integer():
            default = int(default)
        if isinstance(default, bool):
            default = "true" if default else "false"
        elif isinstance(default, list):
            default = ", ".join(default)
        out.append(f"# {f.name} = {default}")
    return "\n".join(out) + "\n"
