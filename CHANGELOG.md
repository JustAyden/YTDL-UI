# Changelog

## [2.1.0] — 2026-11-15

### New
- **Server bandwidth cap** (`bandwidth_limit`, default **10 MB/s**, `0` = unlimited) so heavy use can't
  starve the server's network connection. Finished files, playlist ZIPs, relayed browser-mode streams
  and the self-hosted ffmpeg.wasm core share one server-wide limit; server-side yt-dlp downloads are
  capped at the same total, split evenly across `max_concurrent_jobs` (5 MB/s each with the defaults).
  Set it in `config.ini`, with `YTDLUI_BANDWIDTH_LIMIT`, or `--set bandwidth_limit=25`; decimals work.
- Admin panel shows the cap and how fast the server is sending right now.

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
