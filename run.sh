#!/usr/bin/env bash
# Start the photo server. Creates the venv on first run.
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "Setting up the virtual environment (first run only)…"
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

# Show the address guests on the same wifi should use.
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)"
PORT="${PORT:-8000}"
if [ -n "${LAN_IP:-}" ]; then
  echo ""
  echo "  On this network, guests can reach you at: http://${LAN_IP}:${PORT}"
  echo "  (set BASE_URL in .env to this before generating QR codes)"
fi

exec .venv/bin/uvicorn app.main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT}" \
  --timeout-keep-alive 75 \
  "$@"
