# YTDL-UI

A clean, self-hosted web UI for downloading YouTube videos and audio — powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ffmpeg](https://ffmpeg.org). No accounts, no tracking, no ads.

![Sample UI Image](https://github.com/JustAyden/YTDL-UI/blob/main/111.png?raw=true)

> Made by [@JustAyden](https://github.com/JustAyden) & [@dash1101](https://github.com/dash1101)

---

## Features

- **Video formats** — MP4, WebM, MKV with resolution selection (1080p, 720p, 480p, …)
- **Audio formats** — MP3, FLAC, WAV, AAC, Opus with bitrate selection
- **Playlist support** — download an entire playlist as a numbered ZIP archive
- **Zero frontend build step** — single `index.html`, no npm, no bundler
- **Cross-platform** — bundles `yt-dlp.exe`/`ffmpeg.exe` on Windows; uses system PATH on Linux/macOS

---

## Quick Start

### Prerequisites

| Tool | Windows | Linux/macOS |
|---|---|---|
| Python 3.10+ | [python.org](https://python.org/downloads) | `apt install python3` / `brew install python` |
| yt-dlp | bundled `.exe` | see below |
| ffmpeg | bundled `.exe` | `apt install ffmpeg` / `brew install ffmpeg` |

**Linux/macOS — install yt-dlp:**
```bash
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o /usr/local/bin/yt-dlp && sudo chmod a+rx /usr/local/bin/yt-dlp
```

### Install Python dependencies

```bash
pip install -r requirements.txt
```

### Run

**Windows:**
```
start.bat
```
or
```
python server.py
```

**Linux / macOS:**
```bash
chmod +x start.sh && ./start.sh
```
or
```bash
python3 server.py
```

Then open **http://localhost:5000** in your browser.

---

## CLI Flags

| Flag | Default | Description |
|---|---|---|
| `-p`, `--port` | `5000` | Port to run the server on |
| `--host` | `127.0.0.1` | Address to bind to |

**Examples:**

```bash
# Default — localhost only (safe for tunnel/proxy setups)
python3 server.py

# Local network access — accessible from other devices on your LAN
python3 server.py --host 0.0.0.0

# Custom port
python3 server.py --port 8080

# Both
python3 server.py --host 0.0.0.0 --port 8080
```

> **Note:** The default bind address is `127.0.0.1` (localhost only). If you're running YTDL-UI on a separate machine and want to access it from another device on your network, pass `--host 0.0.0.0`. If you're running it behind a reverse proxy or Cloudflare tunnel, keep the default.

---

## Windows — Bundled Binaries

Place `yt-dlp.exe` and `ffmpeg.exe` in the same folder as `server.py`. The app detects them automatically.

Download links:
- **yt-dlp.exe** → [github.com/yt-dlp/yt-dlp/releases/latest](https://github.com/yt-dlp/yt-dlp/releases/latest)
- **ffmpeg.exe** → [ffmpeg.org/download.html](https://ffmpeg.org/download.html) (get a Windows build from gyan.dev or BtbN)

---

## Server Deployment

### Reverse proxy / Cloudflare tunnel

YTDL-UI binds to `127.0.0.1` by default, making it safe to expose via a reverse proxy (Caddy, Nginx) or a [Cloudflare Zero Trust tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) without opening any ports directly. Point your proxy or tunnel at `http://127.0.0.1:5000`.

See **[SETUP.md](SETUP.md)** for a full guide on running YTDL-UI behind Caddy on a Debian server with automatic HTTPS.

### Running as a systemd service (Linux)

For a hardened, always-on deployment that runs as an unprivileged user and restarts automatically:

```bash
sudo bash install-service.sh
```

This script:
- Creates a dedicated `ytdlui` system user with no login shell
- Installs the app to `/opt/ytdlui`
- Installs and enables the `ytdlui` systemd service

Useful commands after install:
```bash
journalctl -u ytdlui -f        # live logs
systemctl restart ytdlui       # restart
systemctl stop ytdlui          # stop
```

---

## API

| Endpoint | Method | Body | Description |
|---|---|---|---|
| `/` | GET | — | Serves the web UI |
| `/api/info` | POST | `{url}` | Returns video/playlist metadata |
| `/api/download` | POST | `{url, format, quality}` | Downloads and streams a single file |
| `/api/download-playlist` | POST | `{url, format, quality}` | Downloads playlist, returns ZIP |
| `/api/clear-tmp` | POST | — | Manually clears the temp directory |

---

## License

MIT — do whatever you want with it.
