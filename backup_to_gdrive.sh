#!/usr/bin/env bash
# Daily backup of the tg-watcher SQLite DB to Google Drive via rclone.
# Takes a consistent snapshot of the live WAL DB via the sqlite3 backup API,
# gzips it, uploads with rclone, prunes old backups (keeps newest KEEP).
set -euo pipefail

DB="${CREDDB_DB_PATH:-/opt/tg-watcher/credentials.db}"
REMOTE="${GDRIVE_BACKUP_REMOTE:-tg-gdrive}"
FOLDER="${GDRIVE_BACKUP_FOLDER:-tg-watcher-backups}"
KEEP="${GDRIVE_BACKUP_KEEP:-14}"
LOG=/var/log/tg-backup.log
BASE=/opt/tg-watcher

log(){ echo "$(date '+%F %T') $*" >> "$LOG"; logger -t tg-backup "$*"; }

command -v rclone >/dev/null || { log "rclone not installed"; exit 1; }
rclone listremotes 2>/dev/null | grep -qx "${REMOTE}:" || {
  log "rclone remote '${REMOTE}' not configured - skipping (run rclone config)"
  exit 0
}

exec 9>/run/tg-backup.lock
flock -n 9 || { log "already running"; exit 0; }

STAMP=$(date +%Y%m%d-%H%M)
TMP=$(mktemp -d /tmp/tgbackup.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
OUT="$TMP/credentials-$STAMP.db"

# consistent snapshot even while the watcher is writing (WAL-safe)
if command -v sqlite3 >/dev/null; then
  sqlite3 "$DB" ".backup '$OUT'"
else
  "$BASE/.venv/bin/python" - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
s.backup(d); d.close(); s.close()
PY
fi
gzip -f "$OUT"

SIZE=$(stat -c%s "$OUT.gz")
log "uploading credentials-$STAMP.db.gz ($SIZE bytes) to $REMOTE:$FOLDER"
rclone copy "$OUT.gz" "${REMOTE}:${FOLDER}" --transfers 1 -q
log "upload done"

# prune: filenames embed timestamps so lexicographic sort == chronological
rclone lsf --files-only "${REMOTE}:${FOLDER}" | sort -r | tail -n +$((KEEP+1)) | \
while IFS= read -r f; do
  [ -n "$f" ] || continue
  rclone deletefile "${REMOTE}:${FOLDER}/$f" -q && log "pruned $f"
done
log "backup complete"
