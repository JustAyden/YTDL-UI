# YTDLUI Setup Guide

How to run YTDLUI on Windows (quick start) or deploy it on a Debian server behind Caddy.

---

## Windows — Quick Start

The repo ships with bundled `yt-dlp.exe` and `ffmpeg.exe`, so the only thing you need to install is Python.

### 1. Install Python

Download and install Python 3.10+ from https://www.python.org/downloads/
**Check "Add Python to PATH"** during installation.

### 2. Install Flask

Open a terminal (Command Prompt or PowerShell) in the project folder and run:

```powershell
pip install flask flask-cors
```

### 3. Run

Double-click `start.bat`, or run:

```powershell
python server.py
```

### 4. Open the UI

Navigate to http://localhost:5000 in your browser.

That's it. The bundled `yt-dlp.exe` and `ffmpeg.exe` are detected automatically.

---

## Linux / macOS — Quick Start

### 1. Install dependencies

**Debian / Ubuntu:**
```bash
sudo apt install python3 python3-pip ffmpeg
pip3 install flask flask-cors
```

**macOS (Homebrew):**
```bash
brew install python ffmpeg
pip3 install flask flask-cors
```

### 2. Install yt-dlp

```bash
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp
```

### 3. Run

```bash
python3 server.py
```

Open http://localhost:5000.

---

## Debian Server — Production Deployment with Caddy

A guide for running YTDLUI as a permanent service behind Caddy (with automatic HTTPS).

### Prerequisites

- Debian 11 (Bullseye) or Debian 12 (Bookworm)
- Root or `sudo` access
- A domain pointed at your server (optional — plain HTTP also works)

---

### 1. Update the system

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Install Python and ffmpeg

```bash
sudo apt install -y python3 python3-pip python3-venv ffmpeg curl
```

### 3. Install yt-dlp

```bash
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp
yt-dlp --version
```

### 4. Install Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

### 5. Deploy the app

```bash
sudo mkdir -p /srv/ytdlui

# Copy files (adjust path as needed)
sudo cp server.py index.html /srv/ytdlui/

# Python virtual environment
cd /srv/ytdlui
sudo python3 -m venv venv
sudo venv/bin/pip install flask flask-cors

# Temp dir and permissions
sudo mkdir -p /srv/ytdlui/.tmp
sudo chown -R www-data:www-data /srv/ytdlui
```

> `server.py` auto-detects `yt-dlp` and `ffmpeg` from PATH on Linux. No `.exe` files needed.

### 6. Create a systemd service

Create `/etc/systemd/system/ytdlui.service`:

```ini
[Unit]
Description=YTDLUI — yt-dlp web downloader
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/srv/ytdlui
ExecStart=/srv/ytdlui/venv/bin/python server.py
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ytdlui
sudo systemctl status ytdlui
```

View logs:

```bash
sudo journalctl -u ytdlui -f
```

### 7. Configure Caddy

Edit `/etc/caddy/Caddyfile`.

**With a domain (automatic HTTPS):**

```
yourdomain.com {
    reverse_proxy localhost:5000
    encode gzip

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        -Server
    }
}
```

**HTTP only (no domain):**

```
:80 {
    reverse_proxy localhost:5000
    encode gzip
}
```

Reload Caddy:

```bash
sudo systemctl reload caddy
```

### 8. Firewall (ufw)

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### 9. Keep yt-dlp updated

```bash
sudo yt-dlp -U
```

Automate with a weekly cron job:

```bash
echo "0 3 * * 0 root /usr/local/bin/yt-dlp -U --quiet" \
  | sudo tee /etc/cron.d/ytdlp-update
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `yt-dlp: not found` | Ensure `/usr/local/bin/yt-dlp` exists and is executable |
| `ffmpeg: not found` | Run `sudo apt install ffmpeg` (Linux) or `brew install ffmpeg` (macOS) |
| Downloads fail silently | Check `sudo journalctl -u ytdlui -f` and retry |
| Caddy returns 502 | `sudo systemctl status ytdlui` — Flask may have crashed |
| HTTPS cert not issuing | Ports 80 and 443 must be open and the domain must resolve to your IP |
| Playlist downloads time out | Playlist timeout is 2 hours; very large playlists may still exceed this |

---

## File layout (production)

```
/srv/ytdlui/
├── server.py
├── index.html
├── venv/
└── .tmp/         ← auto-created, auto-cleaned after 120s
```
