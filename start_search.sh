#!/bin/bash
# Start the credential search server on VPS
set -e

export CREDDB_DB_PATH=/tmp/vps_db_merge2.db
export CREDDB_WEB_ADMIN_USER=admin
export CREDDB_WEB_ADMIN_PASS=Dirty2020!
export CREDDB_SECRET_KEY=cred-search-prod-key-2026-$(hostname)
export CREDDB_PORT=8081
export PYTHONPATH=/tmp

cd /tmp
exec python3 -u search_server.py
