import sqlite3, os, sys
sys.path.insert(0, '/tmp')

db = '/tmp/vps_db_merge2.db'
c = sqlite3.connect(db)
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA synchronous=NORMAL")

cred = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
idx = c.execute("SELECT COUNT(*) FROM credentials_fts_idx").fetchone()[0]
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
size_mb = os.path.getsize(db) / 1024 / 1024
print(f"DB size: {size_mb:.1f}MB")
print(f"Credentials: {cred:,}")
print(f"FTS idx rows: {idx}")
print(f"Tables: {tables}")

# Quick search test
from credentials_db import search
from pathlib import Path
results, total = search(Path(db), 'gmail', 3, 0)
print(f"Search 'gmail': {total:,} results")
c.close()
