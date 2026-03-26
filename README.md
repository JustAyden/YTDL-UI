# YTDL-UI

A clean, self-hosted web UI for downloading YouTube videos and audio — powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ffmpeg](https://ffmpeg.org). No accounts, no tracking, no ads.

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

## Windows — Bundled Binaries

Place `yt-dlp.exe` and `ffmpeg.exe` in the same folder as `server.py`. The app detects them automatically.

Download links:
- **yt-dlp.exe** → [github.com/yt-dlp/yt-dlp/releases/latest](https://github.com/yt-dlp/yt-dlp/releases/latest)
- **ffmpeg.exe** → [ffmpeg.org/download.html](https://ffmpeg.org/download.html) (get a Windows build from gyan.dev or BtbN)

---

## Server Deployment

See **[SETUP.md](SETUP.md)** for a full guide on running YTDL-UI behind Caddy on a Debian server with automatic HTTPS.

---

## Project Structure

```
YTDL-UI/
├── server.py        Flask backend (API + static serving)
├── index.html       Frontend — single file, no build needed
├── requirements.txt Python dependencies
├── start.bat        Windows launcher
├── start.sh         Linux/macOS launcher
├── SETUP.md         Server deployment guide
└── CLAUDE.md        AI assistant context
```

---

## API

| Endpoint | Method | Body | Description |
|---|---|---|---|
| `/` | GET | — | Serves the web UI |
| `/api/info` | POST | `{url}` | Returns video/playlist metadata |
| `/api/download` | POST | `{url, format, quality}` | Downloads and streams a single file |
| `/api/download-playlist` | POST | `{url, format, quality}` | Downloads playlist, returns ZIP |

---

## License

MIT — do whatever you want with it.
