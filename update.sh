#!/bin/sh
# Update YTDL-UI (git pull or release download) and yt-dlp, then restart it.
#   ./update.sh            — source install in this folder
#   sudo ./update.sh       — the systemd install in /opt/ytdlui (restarts the service)
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

if [ "$(id -u)" = "0" ] && [ -d /opt/ytdlui ] && [ "$(pwd)" != "/opt/ytdlui" ]; then
    cd /opt/ytdlui
    PY=/opt/ytdlui/.venv/bin/python
fi

if [ "$(id -u)" = "0" ] && id ytdlui >/dev/null 2>&1 && [ "$(pwd)" = "/opt/ytdlui" ]; then
    # Run as the service user so file ownership stays correct.
    su -s /bin/sh ytdlui -c "$PY server.py --update"
    systemctl restart ytdlui && echo "Restarted the ytdlui service."
else
    "$PY" server.py --update
    echo "If YTDL-UI is running, restart it to finish."
fi
