@echo off
setlocal
title YTDL-UI
cd /d "%~dp0"

rem Start YTDL-UI on Windows. First run creates .venv and installs everything.
rem Usage: start.bat [server options]     e.g.  start.bat --host 0.0.0.0 --port 8080

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python 3.10+ not found.
    echo   Install it from https://python.org/downloads and tick "Add python.exe to PATH",
    echo   or download the ready-made ytdl-ui-windows-x64.exe from the GitHub releases page.
    pause
    exit /b 1
)

%PY% -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating .venv ...
    %PY% -m venv .venv || (echo [ERROR] Could not create .venv & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
)

if not exist ".venv\.requirements" (
    echo [SETUP] Installing dependencies ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
    type nul > ".venv\.requirements"
)

".venv\Scripts\python.exe" server.py %*
if errorlevel 1 pause
