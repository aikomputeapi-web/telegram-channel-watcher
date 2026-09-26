import os, glob, sqlite3, time

uploads = r"C:\Users\Administrator\Downloads\tg-watcher\uploads"
target = os.path.join(uploads, "vps_db_merge2.db")

c = sqlite3.connect(target)
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA synchronous=NORMAL")

# Already ingested msg_ids from the DB itself
ingested = set(r[0] for r in c.execute("SELECT DISTINCT msg_id FROM credentials").fetchall())
before = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
print(f"DB starts with: {before:,} rows, {len(ingested)} msg_ids ingested")

# Create unique index if not exists (for INSERT OR IGNORE dedup)
try:
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cred_dedup ON credentials(domain, username, password)")
    c.commit()
except Exception as e:
    print(f"Index creation failed (dupes exist): {e}")
    print("Deduping first...")
    c.execute("""DELETE FROM credentials WHERE rowid NOT IN (
        SELECT MIN(rowid) FROM credentials GROUP BY domain, username, password)""")
    c.commit()
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cred_dedup ON credentials(domain, username, password)")
    c.commit()

current = before
total_new = 0

for d in sorted(os.listdir(uploads)):
    if not d.endswith('.parsed'):
        continue
    full = os.path.join(uploads, d)
    if not os.path.isdir(full):
        continue
    try:
        msg_id = int(d.split('_')[0])
    except:
        continue

    if msg_id in ingested:
        continue

    cred_files = sorted(glob.glob(os.path.join(full, "**", "credentials.txt"), recursive=True))
    if not cred_files:
        print(f"  msg {msg_id}: no credentials.txt, skipping")
        continue

    rows = []
    for cf in cred_files:
        part_name = os.path.basename(os.path.dirname(cf))
        rel_path = os.path.relpath(cf, full)
        with open(cf, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.rstrip('\n')
                if not line:
                    continue
                parts = line.split(':', 2)
                if len(parts) < 2:
                    continue
                domain = parts[0]
                username = parts[1] if len(parts) > 1 else ''
                password = parts[2] if len(parts) > 2 else ''
                if '\n' in username or '\r' in username or '\n' in password or '\r' in password:
                    continue
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
    print(f"  msg {msg_id}: +{added:,} new rows ({len(cred_files)} files) -> total: {after:,}")

c.close()

final = sqlite3.connect(target)
total = final.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
size_mb = os.path.getsize(target) / 1024 / 1024
print(f"\n=== FINAL: {total:,} unique rows ({size_mb:.1f}MB). Added {total_new:,} new. ===")
final.close()
