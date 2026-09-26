import os, sqlite3

# Search entire filesystem for SQLite databases with credentials table
found = []
for root, dirs, files in os.walk('/'):
    # Skip proc, sys, dev, snap
    dirs[:] = [d for d in dirs if d not in ('proc', 'sys', 'dev', 'snap', 'run', 'boot', 'lib', 'bin', 'sbin', 'usr', 'opt', 'etc', 'var/lib', 'var/log')]
    for fn in files:
        if fn.endswith(('.db', '.sqlite', '.sqlite3', '.db-journal', '.db-wal')):
            fp = os.path.join(root, fn)
            try:
                size_mb = os.path.getsize(fp) / 1024 / 1024
                if size_mb < 0.1:
                    continue
                c = sqlite3.connect(fp)
                tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if 'credentials' in tables:
                    rows = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
                    found.append((fp, size_mb, rows))
                    print(f"CREDS: {fp}  ({size_mb:.1f}MB, {rows} rows)")
                c.close()
            except:
                pass

print(f"\n=== Summary: {len(found)} credential databases found ===")
total = sum(r[2] for r in found)
print(f"Total credential rows across all DBs: {total:,}")
