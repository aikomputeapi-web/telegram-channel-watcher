

## cred-search outage post-mortem (2026-09-24)

Outcome: https://cred-search.nowrouter.store/ RESTORED (HTTP 200, login page live).

Root cause chain:
1. Tunnel 48beabcf (nowrouter) died with the Azure freetier VM deallocation Sept 13 - its public IP 13.66.154.235 was lost.
2. Half-finished migration Sept 13 created tunnel 0bd0c623 (cred-search-vps) but never repointed DNS; user kept hitting dead 48beabcf => Cloudflare 530.
3. 2026-09-24: pushed ingress to 0bd0c623 (cred-search -> http://localhost:8081, ssh-credsearch -> ssh://localhost:22), repointed DNS CNAME to the 0bd0c623 cfargotunnel host. Site 530 -> 200 same hour.

Current state:
- Live box: Azure freetier VM (hostname freetier), public IP changed (currently undocumented), cloudflared v2026.9.1, 4 conns on tunnel 0bd0c623.
- App: cred-search systemd unit; env per Sept-13 dump: CREDDB_DB_PATH=/opt/tg-watcher/credentials.db, admin admin/Dirty2020!. NOTE: live site currently REJECTS admin/Dirty2020! over curl - verify when SSH returns.
- SSH: over-tunnel works (ProxyCommand cloudflared access ssh --hostname ssh-credsearch.nowrouter.store) but freetier_key.pem REJECTED; Azure portal password reset pending.
- Azure CLI installed (az 2.90.0); az login still needed for run-command as SSH-independent shell access.
