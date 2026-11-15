#!/bin/sh
# Start YTDL-UI on Linux, macOS, *BSD or Android (Termux).
# First run creates a private Python environment in .venv and installs everything.
# Usage: ./start.sh [server options]      e.g.  ./start.sh --host 0.0.0.0 --port 8080
set -e
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "[ERROR] Python 3.10+ is required."
    echo "  Debian/Ubuntu: sudo apt install python3 python3-venv"
    echo "  Fedora:        sudo dnf install python3"
    echo "  Arch:          sudo pacman -S python"
    echo "  macOS:         brew install python   (or https://python.org/downloads)"
    echo "  FreeBSD:       pkg install python3"
    echo "  Termux:        pkg install python"
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "[SETUP] Creating .venv ..."
    if ! "$PY" -m venv .venv 2>/dev/null; then
        echo "[ERROR] Couldn't create a virtual environment."
        echo "  Debian/Ubuntu: sudo apt install python3-venv"
        exit 1
    fi
    .venv/bin/python -m pip install --upgrade pip >/dev/null
fi

STAMP=.venv/.requirements
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
    echo "[SETUP] Installing dependencies ..."
    .venv/bin/python -m pip install -r requirements.txt
    touch "$STAMP"
fi

command -v ffmpeg >/dev/null 2>&1 || [ -x ./ffmpeg ] || [ -x data/bin/ffmpeg ] || \
    echo "[WARN] ffmpeg not found — install it (apt/dnf/brew/pkg install ffmpeg) or run: ./start.sh --install-ffmpeg"
command -v deno >/dev/null 2>&1 || command -v node >/dev/null 2>&1 || \
    echo "[WARN] No JavaScript runtime (deno or node) — YouTube may only offer low qualities. See README."

exec .venv/bin/python server.py "$@"
