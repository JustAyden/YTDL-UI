@echo off
title YTDL-UI

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install it from https://python.org and add it to PATH.
    pause
    exit /b 1
)

python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [SETUP] Installing dependencies...
    pip install -r requirements.txt
)

echo.
echo  Starting YTDL-UI...
echo.

python server.py %*
pause