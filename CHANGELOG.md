# Changelog

## [2.2.0] — 2026-11-15

### New
- **Two-factor authentication (TOTP) for the admin panel.** Works with any authenticator app
  (Google/Microsoft Authenticator, Aegis, 2FAS, 1Password, Bitwarden…), with a QR code for setup and
  8 one-time recovery codes. Each code can only be used once.
- **Guided first login.** The first time an admin opens the panel they must turn on 2FA, then replace
  the initial password. The initial password comes from `admin_password` or, if that's empty, is
  generated on first start and printed to the log and `data/initial-admin-password.txt`.
- **Change the admin password from the panel** — requires the current password and a 2FA code;
  every other admin session is signed out. Recovery codes can be regenerated the same way.
- **Brute-force lockouts** for the admin and site passwords: after `login_max_attempts` (5) failures an
  IP is locked out for `login_lockout_minutes` (15), doubling on every repeat (max 24 h). Lockouts
  survive restarts; admins can see and lift them in the panel.
- `--reset-admin` to recover a lost admin login from the server's command line.
- The admin panel is now on by default (`admin_enabled = true`).

### Upgrading from 2.1
- Your existing `admin_password` becomes the initial password: the next admin login walks you through
  2FA setup and asks for a new password. After that, `admin_password` in the config is no longer used.

## [2.1.0] — 2026-11-15

### New
- **Server bandwidth cap** (`bandwidth_limit`, default **10 MB/s**, `0` = unlimited) so heavy use can't
  starve the server's network connection. Finished files, playlist ZIPs, relayed browser-mode streams
  and the self-hosted ffmpeg.wasm core share one server-wide limit; server-side yt-dlp downloads are
  capped at the same total, split evenly across `max_concurrent_jobs` (5 MB/s each with the defaults).
  Set it in `config.ini`, with `YTDLUI_BANDWIDTH_LIMIT`, or `--set bandwidth_limit=25`; decimals work.
- Admin panel shows the cap and how fast the server is sending right now.

### Fixed
- Windows: the server crashed at startup when its output wasn't a console window (Windows services,
  scheduled tasks, redirected logs, CI) because the startup banner contained a character the legacy
  code page can't print. Console output now never crashes on unprintable characters. This blocked
  the v2.0.0 Windows builds, so 2.1.0 is the first v2 release with Windows apps.

## [2.0.0] — 2026-11-15

A ground-up refresh: background downloads with real progress, optional in-browser processing,
an update system, standalone apps for every major OS, and a lot of hardening.

### New
- **Browser processing.** Admins can let visitors' devices do the merging/converting with
  ffmpeg.wasm (`processing_mode = server | browser | choice`). The server then only resolves
  streams with yt-dlp and, for sites like YouTube that block direct browser access, streams the
  bytes through without touching disk or ffmpeg. Falls back to the server automatically when a
  link needs it (configurable).
- **Update system.** yt-dlp updates itself automatically (pip or verified release binary).
  YTDL-UI checks GitHub releases, notifies admins and can update via the admin panel,
  `--update`, `update.sh`/`update.bat`, or by swapping its own executable.
- **Standalone apps** for Windows (x64, ARM64), macOS (Apple Silicon, Intel) and Linux (x64, ARM64),
  plus a multi-arch Docker image with ffmpeg and Deno included.
- **Background jobs with real progress** — speed, ETA, stage, cancel; survive page reloads; queue
  with concurrency limits. Works behind proxies with short timeouts (e.g. Cloudflare's 100 s).
- **Playlist picker** — choose individual items; skipped/unavailable items are reported.
  Playlist ZIPs are streamed instead of built on disk (no doubled disk usage), and every file can
  also be saved on its own.
- **Audio**: M4A and Opus added (lossless copy when possible); embedded title/artist/cover art.
  Bitrate is hidden for lossless formats.
- **Admin panel** — versions, one-click updates, ffmpeg install, server stats, running downloads.
- Optional **site password**, **site allow-list**, duration/size/playlist limits, per-client job limits.
- Config file (`config.ini`), `YTDLUI_*` environment variables and `--set name=value`.
- Light theme, mobile layout, installable web app, share-to-YTDL-UI on Android, `?url=` links.
- Test suite and CI on Windows, macOS and Linux.

### Fixed
- SSRF protection now resolves hostnames and checks every address (the old hostname regex let
  `localtest.me`, `2130706433`, `169.254.169.254`, IPv6-mapped and CGNAT addresses through).
- Rate limiting saw every visitor as `127.0.0.1` behind a reverse proxy; `trust_proxy` fixes that.
- The hourly temp-folder purge could delete downloads that were still running.
- Anyone could call `/api/clear-tmp` and wipe other users' downloads (now admin-only).
- "Download" on a pure playlist link downloaded the whole playlist on the server and returned one file.
- Finished files were buffered entirely in browser memory before saving; they now go straight to
  the browser's download manager.
- README referred to a non-existent `install-service.sh`.
- AAC downloads were served with the wrong file type.

### Changed
- Requires Python 3.10+; `flask-cors` dropped; served by `waitress` (falls back to Flask's server).
- The web UI moved to `static/` (still no build step).
- Old `/api/download` and `/api/download-playlist` endpoints still work for scripts.

## [1.2.1]
- Security patch & self-hosting improvements: URL validation, rate limiting, `--host`, systemd service.

## [1.2.0]
- Playlist ZIP downloads, platform list, `--port`.

## [1.1.0] and earlier
- Initial releases.
