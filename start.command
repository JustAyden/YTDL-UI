#!/bin/sh
# macOS: double-click this file in Finder to start YTDL-UI (opens your browser).
cd "$(dirname "$0")"
exec ./start.sh --open "$@"
