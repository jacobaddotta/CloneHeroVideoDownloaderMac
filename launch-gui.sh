#!/bin/zsh
# Shared launcher used by both start.command and the .app bundle.
set -e
HERE="${0:A:h}"
cd "$HERE"

# An app launched from Finder gets a bare PATH and would not find ffmpeg,
# node or deno. Put Homebrew back on the path explicitly.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

VENV="$HERE/.venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "First run: setting up the Python environment..."
  BASE=""
  for c in /opt/homebrew/opt/python@3.13/bin/python3.13 \
           /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3 /usr/bin/python3; do
    [ -x "$c" ] && BASE="$c" && break
  done
  if [ -z "$BASE" ]; then
    echo "Could not find a Python 3 to build the environment."
    read "?Press return to close."
    exit 1
  fi
  "$BASE" -m venv "$VENV"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r "$HERE/requirements.txt"
  echo "Setup complete."
fi

# ffmpeg is required for both syncing and merging downloads.
if ! command -v ffmpeg >/dev/null 2>&1; then
  osascript -e 'display alert "ffmpeg is missing" message "Install it with:\n\nbrew install ffmpeg" as critical' >/dev/null 2>&1 || true
  echo "ffmpeg not found. Install with: brew install ffmpeg"
  read "?Press return to close."
  exit 1
fi

exec "$PY" "$HERE/gui.py"
