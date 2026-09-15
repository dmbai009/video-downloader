@echo off
rem Launcher for Windows. On macOS and Linux use start.sh instead.
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1 && (set PY=python) || (set PY=py)

start "" http://localhost:8777
%PY% server.py

echo.
echo Server stopped.
pause
