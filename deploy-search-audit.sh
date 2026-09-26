#!/bin/bash
# Deploy search-audit feature (admin search-log review) to the cred-search VPS.
#
# What this does:
#   1. Scp the updated search_server.py + credentials_db.py to /tmp on the VPS.
#   2. Backs up the current /tmp copies (one-time, timestamped).
#   3. Restarts the cred-search systemd service.
#      -> init_db() runs at startup and CREATE TABLE IF NOT EXISTS search_log (...)
#         adds the new audit-log table to the existing DB with zero data loss.
#   4. Confirms the service is active and the search_log table exists.
#
# Usage:
#   ./deploy-search-audit.sh                       # uses VPS_PW env var
#   VPS_PW='yourpassword' ./deploy-search-audit.sh  # inline
#
# VPS: root@204.44.93.145 (Dedirock). DB: /tmp/vps_db_merge2.db. Port 8081.
set -euo pipefail

VPS_HOST="${VPS_HOST:-204.44.93.145}"
VPS_USER="${VPS_USER:-root}"
VPS_PW="${VPS_PW:?need VPS root password in VPS_PW}"
REMOTE_DIR="${REMOTE_DIR:-/tmp}"
DB_PATH="${DB_PATH:-/tmp/vps_db_merge2.db}"

HERE="$(cd "$(dirname "$0")" && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"

echo "==> Deploying search-audit to ${VPS_USER}@${VPS_HOST}"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"

# 1. Backup current remote files (only if they exist; non-fatal if not).
echo "==> Backing up current /tmp files (timestamp: $TS)"
sshpass -p "$VPS_PW" ssh $SSH_OPTS "${VPS_USER}@${VPS_HOST}" \
 "cp -n ${REMOTE_DIR}/search_server.py ${REMOTE_DIR}/search_server.py.bak-${TS} 2>/dev/null || true;
   cp -n ${REMOTE_DIR}/credentials_db.py ${REMOTE_DIR}/credentials_db.py.bak-${TS} 2>/dev/null || true"

# 2. Push updated source.
echo "==> Uploading updated search_server.py + credentials_db.py"
sshpass -p "$VPS_PW" scp $SSH_OPTS \
 "$HERE/search_server.py" "$HERE/credentials_db.py" \
 "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"

# 3. Restart the service. init_db() at startup creates the search_log table.
echo "==> Restarting cred-search service"
sshpass -p "$VPS_PW" ssh $SSH_OPTS "${VPS_USER}@${VPS_HOST}" \
 "systemctl restart cred-search; sleep 2; systemctl is-active cred-search"

# 4. Verify the search_log table was created in the DB.
echo "==> Verifying search_log table in ${DB_PATH}"
sshpass -p "$VPS_PW" ssh $SSH_OPTS "${VPS_USER}@${VPS_HOST}" \
 "python3 -c \"import sqlite3; c=sqlite3.connect('${DB_PATH}'); print('search_log table:', bool(c.execute(\\\"SELECT name FROM sqlite_master WHERE type='table' AND name='search_log'\\\").fetchone())); print('credentials rows:', c.execute('SELECT COUNT(*) FROM credentials').fetchone()[0])\""

echo ""
echo "==> Done. Live at https://cred-search.nowrouter.store"
echo "==> Log in as admin, then click 'Search Log' in the nav to review all searches."
