import sqlite3, sys, time
sys.path.insert(0, "/opt/tg-watcher")
from credentials_db import search

DB = "/opt/tg-watcher/downloads/db-recovered-20260924.db"
c = sqlite3.connect(DB)

# grab distinct rows from the final gap-period msg (1870)
rows = c.execute("SELECT id, domain, username, password FROM credentials WHERE msg_id=1870 AND LENGTH(username) >= 8 ORDER BY id DESC LIMIT 5").fetchall()
print(f"test rows from msg 1870 (top rowids): {[r[0] for r in rows]}")
found = 0
for rid, dom, user, pw in rows:
    # substring that exists in username AND is long enough for trigram
    needle = user[3:10]
    if len(needle) < 3:
        continue
    times = []
    t0 = time.time()
    try:
        results, total = search(DB, needle, 50, 0)
    except Exception as e:
        print(f"  '{needle}': SEARCH ERROR {e}")
        continue
    dt = time.time() - t0
    hit_ids = set()
    for r in results:
        # search() result tuple: find username column position
        if user in tuple(r):
            hit_ids.add(1)
    ok = any(needle in str(x) and user in str(x) for r in results for x in (r if isinstance(r, (tuple, list)) else (r,)))
    print(f"  '{needle}' (user={user[:20]}): total={total:,} dt={dt:.2f}s sample-match={ok}")
    if ok:
        found += 1
print(f"RESULT: {found}/{len(rows)} gap rows found via FTS")
