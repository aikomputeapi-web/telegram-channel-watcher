import sqlite3, time, sys, os
sys.path.insert(0, r"C:\Users\Administrator\coding\telegram-channel-watcher")
from credentials_db import init_db, search
from pathlib import Path

db = Path(r"C:\Users\Administrator\Downloads\tg-watcher\uploads\vps_db_merge2.db")

print(f"Rebuilding FTS5 index on {db} ({os.path.getsize(db)/1024/1024:.0f}MB)...")
t0 = time.time()
init_db(db)
print(f"FTS5 init done in {time.time()-t0:.1f}s")

# Verify
c = sqlite3.connect(str(db))
idx = c.execute("SELECT COUNT(*) FROM credentials_fts_idx").fetchone()[0]
cred = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
print(f"FTS idx rows: {idx}")
print(f"Credentials rows: {cred}")

# Test searches
for q in ['gmail', 'yahoo', 'bank', 'paypal']:
    results, total = search(db, q, 5, 0)
    print(f"Search '{q}': {total:,} results")
c.close()
