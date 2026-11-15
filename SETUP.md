# YTDL-UI setup guide

Step-by-step instructions for every way of running YTDL-UI. For an overview, settings and
troubleshooting see the [README](README.md).

- [Windows](#windows)
- [macOS](#macos)
- [Linux desktop](#linux-desktop)
- [Debian / Ubuntu server with Caddy (HTTPS)](#debian--ubuntu-server-with-caddy-https)
- [Cloudflare Tunnel](#cloudflare-tunnel)
- [Docker](#docker)
- [Raspberry Pi, FreeBSD, Android (Termux)](#raspberry-pi-freebsd-android-termux)
- [Recommended settings for a public instance](#recommended-settings-for-a-public-instance)
- [Maintenance](#maintenance)

---

## Windows

**Easiest:** download `ytdl-ui-windows-x64.exe` (or `-arm64.exe` for ARM laptops) from the
[latest release](https://github.com/JustAyden/YTDL-UI/releases/latest), put it in its own folder
and double-click it. A console window shows the log and your browser opens at
http://localhost:5000. On first start it downloads ffmpeg into `data\bin\` by itself.
Close the console window to stop it.

**From source:**

1. Install Python 3.10+ from [python.org](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"**.
2. Optional but recommended for YouTube: install [Deno](https://deno.land) (`winget install DenoLand.Deno`) or Node.js.
3. Download the repository (green **Code → Download ZIP**, or `git clone`), then double-click **`start.bat`**.
   The first run creates `.venv\` and installs everything.
4. ffmpeg: `start.bat --install-ffmpeg`, or drop `ffmpeg.exe` next to `server.py`.

To make it reachable from other devices on your network: `start.bat --host 0.0.0.0`.

---

## macOS

**App:** download `ytdl-ui-macos-arm64` (Apple Silicon) or `ytdl-ui-macos-x64` (Intel), then in Terminal:

```bash
cd ~/Downloads
xattr -d com.apple.quarantine ytdl-ui-macos-arm64   # the app isn't code-signed
chmod +x ytdl-ui-macos-arm64
./ytdl-ui-macos-arm64
brew install ffmpeg deno                            # if you don't have them yet
```

**From source:** `brew install python ffmpeg deno`, download the repository, then double-click
**`start.command`** (or run `./start.sh` in Terminal).

---

## Linux desktop

```bash
sudo apt install python3 python3-venv ffmpeg     # Fedora: dnf install python3 ffmpeg-free · Arch: pacman -S python ffmpeg
curl -fsSL https://deno.land/install.sh | sh     # JavaScript runtime for YouTube (or install nodejs)
git clone https://github.com/JustAyden/YTDL-UI && cd YTDL-UI
./start.sh
```

Or download `ytdl-ui-linux-x64` / `ytdl-ui-linux-arm64` from the releases, `chmod +x` it and run it.

---

## Debian / Ubuntu server with Caddy (HTTPS)

Tested on Debian 12/13 and Ubuntu 22.04/24.04. You need root and, for HTTPS, a domain pointing at the server.

### 1. Install YTDL-UI as a service

```bash
sudo apt install -y git
git clone https://github.com/JustAyden/YTDL-UI /tmp/ytdl-ui
sudo sh /tmp/ytdl-ui/install.sh
```

`install.sh`:

- installs Python, ffmpeg and friends with your package manager
- creates an unprivileged `ytdlui` system user
- installs the app to `/opt/ytdlui` with its own virtual environment
- creates `/opt/ytdlui/config.ini` from the example
- installs and starts the hardened `ytdlui` systemd service on `127.0.0.1:5000`

Install a JavaScript runtime so YouTube offers all qualities:

```bash
curl -fsSL https://deno.land/install.sh | sudo DENO_INSTALL=/usr/local sh
sudo systemctl restart ytdlui
```

### 2. Configure

Edit `/opt/ytdlui/config.ini` — at least:

```ini
[server]
trust_proxy = 1

[security]
# password = something-long        # makes the instance private
admin_password = something-else    # enables the ⚙ admin panel
```

Then `sudo systemctl restart ytdlui`.

### 3. Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```caddy
ytdl.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:5000 {
        flush_interval -1   # pass downloads and relayed streams through immediately
    }
}
```

`sudo systemctl reload caddy` — Caddy gets a certificate automatically.

### 4. Firewall

```bash
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
```

Port 5000 stays closed: the app only listens on `127.0.0.1`.

<details>
<summary>Nginx instead of Caddy</summary>

```nginx
server {
    server_name ytdl.example.com;
    client_max_body_size 1m;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;            # stream downloads and relayed media
        proxy_read_timeout 1h;
        proxy_send_timeout 1h;
    }
    # listen 443 ssl; … (certbot --nginx adds this)
}
```
</details>

---

## Cloudflare Tunnel

No open ports at all: install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/),
create a tunnel and point a public hostname at `http://127.0.0.1:5000`. In `config.ini` set
`trust_proxy = cloudflare` so rate limits use the visitor's real IP.

Long playlist downloads are fine: they run as background jobs, so Cloudflare's 100-second request
limit never applies. Very large single files are delivered with resumable range requests.

---

## Docker

```bash
docker compose up -d          # uses docker-compose.yml from this repo
# or
docker run -d --name ytdl-ui -p 5000:5000 -v ytdlui-data:/app/data \
  -e YTDLUI_ADMIN_PASSWORD=change-me ghcr.io/justayden/ytdl-ui:latest
```

The image (amd64 + arm64) includes ffmpeg and Deno. Any setting can be passed as `YTDLUI_<NAME>`;
a `config.ini` can be mounted at `/app/config.ini`. Behind a reverse proxy publish only to
localhost (`127.0.0.1:5000:5000`) and set `YTDLUI_TRUST_PROXY=1`.

Update with `docker compose pull && docker compose up -d` (yt-dlp also updates itself inside the
running container between image releases).

---

## Raspberry Pi, FreeBSD, Android (Termux)

- **Raspberry Pi 4/5 with a 64-bit OS:** use `ytdl-ui-linux-arm64`, the Docker image, or `install.sh`.
  32-bit Raspberry Pi OS: run from source with `./start.sh`. On small boards consider
  `processing_mode = browser` so phones and laptops do the converting.
- **FreeBSD:** `pkg install python3 ffmpeg node` then `./start.sh`.
- **Android (Termux):** `pkg install python ffmpeg nodejs git`, clone the repo, `./start.sh`, and open
  http://localhost:5000 in your phone's browser. Use `termux-setup-storage` and
  `--set data_dir=/sdcard/Download/ytdl-ui` if you want the temp files on shared storage.

---

## Recommended settings for a public instance

```ini
[server]
trust_proxy = 1

[security]
admin_password = …
# password = …                       # private instance
rate_limit = 20
download_rate_limit = 5
# allowed_sites = youtube.com, youtu.be, music.youtube.com, soundcloud.com

[limits]
max_concurrent_jobs = 2
max_jobs_per_client = 2
max_duration_minutes = 180
max_filesize_mb = 4096
max_playlist_items = 50
job_ttl_minutes = 20
bandwidth_limit = 10                # MB/s for all user traffic; 0 = unlimited

[processing]
processing_mode = choice
processing_default = auto           # capable devices process locally
```

For the lowest possible server load: `processing_mode = browser` and `fallback_to_server = false`.
The server then never downloads or converts anything — it only resolves stream URLs and relays bytes
for sites (like YouTube) that don't allow browsers to fetch them directly. Set `relay = false` as
well if you only want sites the browser can fetch on its own.

---

## Maintenance

| Task | Command |
|---|---|
| Logs | `journalctl -u ytdlui -f` |
| Restart | `sudo systemctl restart ytdlui` |
| Update YTDL-UI + yt-dlp | `sudo /opt/ytdlui/update.sh` (or the admin panel) |
| Update only yt-dlp | happens automatically; force it with `sudo -u ytdlui /opt/ytdlui/.venv/bin/python /opt/ytdlui/server.py --update-ytdlp` |
| Versions | `sudo -u ytdlui /opt/ytdlui/.venv/bin/python /opt/ytdlui/server.py --version` |
| Effective settings | `… server.py --print-config` |
| Uninstall | `sudo systemctl disable --now ytdlui && sudo rm -rf /opt/ytdlui /etc/systemd/system/ytdlui.service && sudo userdel ytdlui` |

Temporary files live in `/opt/ytdlui/data/tmp` and are deleted `job_ttl_minutes` after each download
finishes (and whenever the service starts). Nothing else needs cleaning up.
