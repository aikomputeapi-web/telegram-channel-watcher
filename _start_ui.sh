#!/bin/bash
printf '\nCREDDB_DB_PATH=/opt/tg-watcher/credentials.db\nCREDDB_PORT=8081\n' >> /opt/tg-watcher/.env
systemctl enable --now cred-search
sleep 4
systemctl is-active cred-search
curl -s -o /dev/null -w 'local UI: %{http_code}\n' http://127.0.0.1:8081/
