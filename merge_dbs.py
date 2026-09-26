import sqlite3, os, time

target = r"C:\Users\Administrator\Downloads\tg-watcher\uploads\vps_db_merge2.db"
sources = [
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\vps_db_merge.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\credentials_all.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\1631__UP_DAISYCLOUD_CHAMPIONING__14_AUGUST_5600_ON_CHANNEL.parsed.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\1632_processed_b147dc2d.parsed.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\1633_15.08_-__WORMCLOUD.parsed.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\1634_6410_15.08.2026__PIXELCLOUD3.parsed.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\1635__UP_DAISYCLOUD_CHAMPIONING__15_AUGUST_5858_ON_CHANNEL.parsed.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\1636__KATANA_CLOUD_FREE.parsed.db",
]

c = sqlite3.connect(target)
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA synchronous=NORMAL")

before = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
print(f"Target DB starts with: {before:,} rows")

# Step 1: Dedup the target DB in-place
# Delete duplicates keeping the lowest rowid (first inserted)
print("Deduplicating target DB (keeping first occurrence of each domain+username+password)...")
t0 = time.time()
c.execute("""
    DELETE FROM credentials 
    WHERE rowid NOT IN (
        SELECT MIN(rowid) FROM credentials 
        GROUP BY domain, username, password
    )
""")
c.commit()
deduped = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
removed = before - deduped
print(f"  Removed {removed:,} duplicate rows in {time.time()-t0:.1f}s. Now: {deduped:,} rows")

# Step 2: Create unique index for fast INSERT OR IGNORE
print("Creating unique index on (domain, username, password)...")
t0 = time.time()
c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cred_dedup ON credentials(domain, username, password)")
c.commit()
print(f"  Index created in {time.time()-t0:.1f}s")

# Step 3: Merge each source
current = deduped
for src in sources:
    src_name = os.path.basename(src)
    src_c = sqlite3.connect(src)
    src_count = src_c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    src_cols = [r[1] for r in src_c.execute("PRAGMA table_info(credentials)").fetchall()]
    print(f"\nSource: {src_name} ({src_count:,} rows)")
    src_c.close()

    alias = "src_" + str(hash(src))[-8:].replace('-','')
    c.execute(f"ATTACH DATABASE ? AS {alias}", (src,))

    if 'msg_id' in src_cols:
        cols = [col for col in src_cols if col != 'id']
        col_list = ', '.join(cols)
        t0 = time.time()
        c.execute(f"INSERT OR IGNORE INTO credentials ({col_list}) SELECT {col_list} FROM {alias}.credentials")
        c.commit()
        elapsed = time.time() - t0
        after = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
        added = after - current
        print(f"  +{added:,} new rows (of {src_count:,}) in {elapsed:.1f}s. Total: {after:,}")
        current = after
    else:
        t0 = time.time()
        c.execute(f"INSERT OR IGNORE INTO credentials (domain, username, password, origin) SELECT domain, username, password, source FROM {alias}.credentials")
        c.commit()
        elapsed = time.time() - t0
        after = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
        added = after - current
        print(f"  +{added:,} new rows (of {src_count:,}) in {elapsed:.1f}s. Total: {after:,}")
        current = after

    c.execute(f"DETACH DATABASE {alias}")

c.close()

final = sqlite3.connect(target)
total = final.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
size_mb = os.path.getsize(target) / 1024 / 1024
print(f"\n=== FINAL: {total:,} unique credential rows ({size_mb:.1f}MB) ===")
final.close()
