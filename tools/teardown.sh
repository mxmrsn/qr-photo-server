#!/usr/bin/env bash
# Bring the photos home, then shut the server down.
#
#   tools/teardown.sh <server-ip-or-host>
#
# Backs up first and verifies the copy before it tells you to delete anything.
# It does not delete the Lightsail instance itself — that is a few clicks in
# the console, and a script that can silently destroy the only copy of your
# wedding photos is not worth the convenience.

set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:-}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
REMOTE_PATH="${REMOTE_PATH:-/srv/photos/app/data}"
KEY="${SSH_KEY:+-i $SSH_KEY}"

if [ -z "$HOST" ]; then
  echo "usage: tools/teardown.sh <server-ip-or-host>"
  echo "  env: REMOTE_USER (default ubuntu), REMOTE_PATH, SSH_KEY"
  exit 2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$HOME/Documents/wedding-photos-final-$STAMP"

echo ""
echo "  Pulling everything from $HOST"
echo "  -> $DEST"
echo ""

mkdir -p "$DEST"
# -a preserves timestamps, --partial survives a dropped connection mid-file.
rsync -a --partial --info=progress2 \
      ${KEY:+-e "ssh $KEY"} \
      "$REMOTE_USER@$HOST:$REMOTE_PATH/" "$DEST/"

echo ""
echo "  Verifying..."

remote_count=$(ssh $KEY "$REMOTE_USER@$HOST" "find $REMOTE_PATH -type f | wc -l" | tr -d ' ')
local_count=$(find "$DEST" -type f | wc -l | tr -d ' ')
remote_bytes=$(ssh $KEY "$REMOTE_USER@$HOST" "du -sb $REMOTE_PATH | cut -f1" | tr -d ' ')
local_bytes=$(du -sk "$DEST" | cut -f1)
local_bytes=$((local_bytes * 1024))

echo "    files : remote $remote_count   local $local_count"
echo "    size  : remote $((remote_bytes/1000000)) MB   local $((local_bytes/1000000)) MB"

if [ "$remote_count" != "$local_count" ]; then
  echo ""
  echo "  STOP: file counts differ. Do not delete anything."
  echo "  Re-run this script; rsync will resume where it left off."
  exit 1
fi

# Prove the database opens and says what we expect.
if [ -f "$DEST/wedding.db" ]; then
  python3 - "$DEST/wedding.db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
ok = con.execute("PRAGMA integrity_check").fetchone()[0]
n, b = con.execute("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM media").fetchone()
g = con.execute("SELECT COUNT(DISTINCT guest_name) FROM media").fetchone()[0]
print(f"    database: {ok}  —  {n} items, {b/1e9:.1f} GB, {g} contributors")
PY
fi

echo ""
echo "  Backup complete and verified."
echo ""
echo "  Make a second copy before deleting anything — one copy is not a backup:"
echo "      cp -a \"$DEST\" /Volumes/<your-drive>/"
echo ""
echo "  Then, to stop being charged:"
echo "    1. Lightsail console -> Instances -> your instance -> Manage -> Delete"
echo "    2. Lightsail console -> Networking -> release the static IP"
echo "       (an unattached static IP is billed; a deleted one is not)"
echo "    3. Snapshots tab -> delete any snapshots you made"
echo "    4. Billing -> confirm next month's forecast is \$0"
echo ""
echo "  Keep the domain if you want the link to keep working for a while;"
echo "  point it at a static page once the server is gone."
echo ""
