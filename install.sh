#!/bin/sh
# install.sh — install (or upgrade) YTDL-UI as a hardened systemd service on Linux.
#   sudo sh install.sh
# Installs to /opt/ytdlui, runs as the unprivileged 'ytdlui' user, listens on
# 127.0.0.1:5000 by default (put Caddy/Nginx/cloudflared in front of it).
set -eu

APP_DIR=/opt/ytdlui
SERVICE_USER=ytdlui
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] Run as root: sudo sh install.sh"; exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "[ERROR] systemd not found. Use ./start.sh or Docker instead."; exit 1
fi

say() { printf '\n── %s\n' "$1"; }

say "Checking system packages"
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv ffmpeg ca-certificates curl unzip >/dev/null
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3 ffmpeg-free ca-certificates curl unzip || dnf install -y -q python3 ca-certificates curl unzip
elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm --needed python ffmpeg curl unzip >/dev/null
elif command -v zypper >/dev/null 2>&1; then
    zypper -q install -y python3 ffmpeg curl unzip || true
else
    echo "Unknown package manager — make sure python3 (3.10+) and ffmpeg are installed."
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "[ERROR] Python 3.10+ required"; exit 1; }

say "Service user"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$APP_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "created user $SERVICE_USER"
else
    echo "user $SERVICE_USER exists"
fi

say "Installing app files to $APP_DIR"
mkdir -p "$APP_DIR/data"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
    if [ -d "$SRC_DIR/.git" ] && command -v git >/dev/null 2>&1 && [ ! -e "$APP_DIR/server.py" ]; then
        # Keep it a git checkout so in-place updates are a fast-forward pull.
        git clone -q "$SRC_DIR" "$APP_DIR.tmp" && cp -a "$APP_DIR.tmp/." "$APP_DIR/" && rm -rf "$APP_DIR.tmp"
        git -C "$APP_DIR" remote set-url origin https://github.com/JustAyden/YTDL-UI.git
    else
        for f in server.py requirements.txt config.example.ini update.sh ytdlui.service; do
            cp "$SRC_DIR/$f" "$APP_DIR/"
        done
        rm -rf "$APP_DIR/ytdlui" "$APP_DIR/static"
        cp -r "$SRC_DIR/ytdlui" "$SRC_DIR/static" "$APP_DIR/"
    fi
fi
[ -f "$APP_DIR/config.ini" ] || cp "$APP_DIR/config.example.ini" "$APP_DIR/config.ini"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
chmod 750 "$APP_DIR"
chmod 640 "$APP_DIR/config.ini"

say "Python environment"
su -s /bin/sh "$SERVICE_USER" -c "cd $APP_DIR && python3 -m venv .venv && .venv/bin/python -m pip install -q --upgrade pip && .venv/bin/python -m pip install -q -r requirements.txt"
echo "installed: $(su -s /bin/sh "$SERVICE_USER" -c "$APP_DIR/.venv/bin/python $APP_DIR/server.py --version" | head -2 | tr '\n' ' ')"

if ! command -v deno >/dev/null 2>&1 && ! command -v node >/dev/null 2>&1; then
    say "JavaScript runtime"
    echo "No deno/node found. YouTube needs one for its best formats. Install deno with:"
    echo "  curl -fsSL https://deno.land/install.sh | sudo DENO_INSTALL=/usr/local sh"
fi

say "systemd service"
cp "$SRC_DIR/ytdlui.service" /etc/systemd/system/ytdlui.service
systemctl daemon-reload
systemctl enable --quiet ytdlui
systemctl restart ytdlui
sleep 3
if systemctl is-active --quiet ytdlui; then
    echo "✓ ytdlui is running on 127.0.0.1:5000"
    echo
    echo "  Settings:  $APP_DIR/config.ini   (then: systemctl restart ytdlui)"
    echo "  Logs:      journalctl -u ytdlui -f"
    echo "  Update:    sudo $APP_DIR/update.sh"
    echo "  Proxy/tunnel target: http://127.0.0.1:5000  (set trust_proxy = 1 in config.ini)"
else
    echo "✗ Service failed to start — check: journalctl -u ytdlui -n 50"
    exit 1
fi
