@echo off
setlocal
cd /d "%~dp0"
rem Update YTDL-UI (git pull or release download) and yt-dlp.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" server.py --update
) else (
    python server.py --update
)
echo.
echo If YTDL-UI is running, close it and start it again to finish.
pause
