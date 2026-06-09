#!/bin/bash
# install.sh — Deploy YTDL-UI as a hardened non-root systemd service
# Run as root: sudo bash install.sh

set -euo pipefail

APP_DIR="/opt/ytdlui"
SERVICE_USER="ytdlui"
SERVICE_FILE="ytdlui.service"
PORT=5000

if [[ $EUID -ne 0 ]]; then
    echo "[ERROR] Run as root: sudo bash install.sh"
    exit 1
fi

echo "── Creating service user ──────────────────────────────"
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "✅ Created user: $SERVICE_USER"
else
    echo "ℹ️  User $SERVICE_USER already exists"
fi

echo "── Installing app files ───────────────────────────────"
mkdir -p "$APP_DIR/.tmp"
cp server.py index.html "$APP_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
chmod 750 "$APP_DIR"
chmod 770 "$APP_DIR/.tmp"
echo "✅ Files installed to $APP_DIR"

echo "── Installing dependencies ────────────────────────────"
pip3 install flask flask-cors --break-system-packages 2>/dev/null || \
    pip3 install flask flask-cors
echo "✅ Python dependencies installed"

echo "── Checking yt-dlp ────────────────────────────────────"
if ! command -v yt-dlp &>/dev/null; then
    echo "Installing yt-dlp..."
    curl -sSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
        -o /usr/local/bin/yt-dlp
    chmod a+rx /usr/local/bin/yt-dlp
    echo "✅ yt-dlp installed"
else
    echo "ℹ️  yt-dlp already installed: $(yt-dlp --version)"
fi

echo "── Installing systemd service ─────────────────────────"
cp "$SERVICE_FILE" /etc/systemd/system/
systemctl daemon-reload
systemctl enable ytdlui
systemctl restart ytdlui
echo "✅ Service installed and started"

echo "── Verifying ──────────────────────────────────────────"
sleep 2
if systemctl is-active --quiet ytdlui; then
    echo "✅ ytdlui is running on 127.0.0.1:$PORT"
    echo ""
    echo "  Cloudflare tunnel should point to: http://127.0.0.1:$PORT"
    echo "  Logs: journalctl -u ytdlui -f"
    echo "  Stop: systemctl stop ytdlui"
else
    echo "❌ Service failed to start. Check: journalctl -u ytdlui -n 50"
    exit 1
fi
