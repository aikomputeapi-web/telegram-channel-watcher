#!/usr/bin/env python3
"""Rebuild the production DB for the new VPS.

Base: vps_db_merge2.db (2.87M rows, integrity OK, msg 1603-1660, has FTS).
Then ingest every .parsed dump dir with msg_id > 1660 (the Aug 27 - Sept 13
gap) using credentials_to_db._is_garbage() vault-dump filter.
Result: _recovered_credentials.db in the uploads dir.
"""
import os, sqlite3, sys, time, shutil

sys.path.insert(0, r"C:\Users\Administrator\coding\telegram-channel-watcher")
from credentials_to_db import _is_garbage, MAX_INGEST_PASSWORD_LEN

uploads = r"C:\Users\Administrator\Downloads\tg-watcher\uploads"
target = os.path.join(uploads, "_recovered_credentials.db")
src = os.path.join(uploads, "vps_db_merge2.db")

print(f"[1] copying base DB...", flush=True)
if os.path.exists(target):
    # resume: keep existing partially-merged target (script is idempotent via
    # INSERT OR IGNORE + msg_id skip list)
    print("    target exists - RESUMING (skipping base copy)", flush=True)
else:
    shutil.copy2(src, target)
    # remove any stale WAL/SHM sidecars from the copy source
    for ext in ("-wal", "-shm"):
        p = target + ext
        if os.path.exists(p):
            os.remove(p)

c = sqlite3.connect(target)
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA synchronous=NORMAL")
c.execute("PRAGMA cache_size=-200000")  # 200MB page cache

before = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
ingested = set(r[0] for r in c.execute("SELECT DISTINCT msg_id FROM credentials").fetchall())
print(f"    base DB: {before:,} rows, msg_ids: {sorted(ingested)}", flush=True)

# ensure dedup index exists
try:
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cred_dedup ON credentials(domain, username, password)")
    c.commit()
except Exception as e:
    print(f"    dedup index creation failed: {e}")
    print("    deduping first...", flush=True)
    c.execute("DELETE FROM credentials WHERE rowid NOT IN (SELECT MIN(rowid) FROM credentials GROUP BY domain, username, password)")
    c.commit()
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cred_dedup ON credentials(domain, username, password)")
    c.commit()

current = before
total_new = 0
t0 = time.time()

for d in sorted(os.listdir(uploads)):
    full = os.path.join(uploads, d)
    if not os.path.isdir(full) or not d.endswith('.parsed'):
        continue
    try:
        msg_id = int(d.split('_')[0])
    except ValueError:
        continue
    if msg_id in ingested:
        continue

    from glob import glob
    cred_files = sorted(glob(os.path.join(full, "**", "credentials.txt"), recursive=True))
    if not cred_files:
        print(f"  msg {msg_id}: no credentials.txt, skipping", flush=True)
        continue

    rows = []
    garbage = 0
    part_name = ''
    for cf in cred_files:
        part_name = os.path.basename(os.path.dirname(cf)) if os.path.dirname(cf) != full else ''
        rel_path = os.path.relpath(cf, full)
        with open(cf, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.rstrip('\n')
                if not line:
                    continue
                parts = line.split(':', 2)
                if len(parts) < 2:
                    continue
                password = parts[2] if len(parts) > 2 else ''
                if _is_garbage(password):
                    garbage += 1
                    continue
                domain, username = parts[0], parts[1]
                rows.append((msg_id, domain, username, password, '', '', rel_path, '',
                             '', 1, '', f"{domain}:{username}:{password}", d, part_name))
                if len(rows) >= 5000:
                    c.executemany("""INSERT OR IGNORE INTO credentials
                        (msg_id, domain, username, password, origin, browser,
                         source_file, source_sha256, confidence, duplicate_count,
                         family, record_id, source_dir, part)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
                    c.commit()
                    rows.clear()
    if rows:
        c.executemany("""INSERT OR IGNORE INTO credentials
            (msg_id, domain, username, password, origin, browser,
             source_file, source_sha256, confidence, duplicate_count,
             family, record_id, source_dir, part)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        c.commit()

    after = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    added = after - current
    total_new += added
    current = after
    print(f"  msg {msg_id}: +{added:,} new rows ({garbage:,} garbage filtered) -> total {after:,}", flush=True)

c.close()

c = sqlite3.connect(target)
total = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
print(f"\n[2] FINAL: {total:,} rows. Added {total_new:,} in {time.time()-t0:.0f}s", flush=True)
print("[3] optimizing + vacuuming (this takes a while)...", flush=True)
c.execute("PRAGMA optimize")
c.commit()
c.execute("VACUUM")
c.commit()
size_mb = os.path.getsize(target) / 1024 / 1024
c.close()
print(f"=== DONE: {total:,} rows, {size_mb:.0f} MB -> {target} ===", flush=True)
