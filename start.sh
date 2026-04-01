#!/usr/bin/env bash
set -e

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 not found. Install it and try again."
    exit 1
fi

# Check dependencies
if ! python3 -c "import flask" &>/dev/null; then
    echo "[SETUP] Installing dependencies..."
    pip3 install -r requirements.txt
fi

# Check yt-dlp
if ! command -v yt-dlp &>/dev/null; then
    echo "[WARN] yt-dlp not found in PATH."
    echo "  Install: sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && sudo chmod a+rx /usr/local/bin/yt-dlp"
fi

echo ""
echo " Starting YTDL-UI..."
echo ""

python3 server.py "$@"