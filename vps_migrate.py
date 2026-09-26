import sys, time
sys.path.insert(0, '/tmp')
from credentials_db import init_db, search
from pathlib import Path

db = Path('/tmp/vps_db_merge2.db')

print(f'Starting FTS5 migration on DB at {db}...')
t0 = time.time()
init_db(db)
print(f'Migration done in {time.time()-t0:.1f}s')

# Verify
import sqlite3
c = sqlite3.connect(str(db))
idx = c.execute('SELECT COUNT(*) FROM credentials_fts_idx').fetchone()[0]
cred = c.execute('SELECT COUNT(*) FROM credentials').fetchone()[0]
print(f'FTS idx rows: {idx}')
print(f'Credentials rows: {cred}')

# Test search
results, total = search(db, 'gmail', 5, 0)
print(f'Search gmail: {total} results (showing {len(results)})')
for r in results:
    print(f'  {r["domain"]} | {r["username"]} | {r["password"][:20]}')
