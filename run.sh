#!/usr/bin/env bash
set -e

PYTHON="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

if [ ! -f ".env" ]; then
  echo "Missing .env. Copy .env.example to .env and add keys before running." >&2
fi

if [ -z "${SKIP_INSTALL:-}" ]; then
  "$PYTHON" -m pip install -r requirements.txt
fi

"$PYTHON" bot.py
