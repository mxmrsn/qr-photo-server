#!/usr/bin/env bash
# Wipe every photo, video, note and guest, and start over.
# Takes a timestamped backup first — this is not recoverable otherwise.
set -euo pipefail
cd "$(dirname "$0")"

if [ -d data ]; then
  BACKUP="../qr-photo-backup-$(date +%Y%m%d-%H%M%S)"
  cp -a data "$BACKUP"
  echo "Backed up to $(cd "$BACKUP" && pwd)"
fi

read -r -p "Delete everything in data/ and start fresh? [y/N] " reply
case "$reply" in
  [yY]*) rm -rf data && echo "Cleared. The next start creates an empty album." ;;
  *)     echo "Left alone." ;;
esac
