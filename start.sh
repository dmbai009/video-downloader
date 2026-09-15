#!/usr/bin/env sh
# Launcher for macOS and Linux. On Windows use start.bat instead.
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Python 3 not found. Install it and try again."
  exit 1
fi

exec "$PY" server.py
