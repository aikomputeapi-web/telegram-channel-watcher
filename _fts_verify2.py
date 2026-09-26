import sqlite3, sys, time
sys.path.insert(0, '/opt/tg-watcher')
from pathlib import Path
from credentials_db import search

DB = Path('/opt/tg-watcher/downloads/db-recovered-20260924.db')
c = sqlite3.connect(str(DB))
rows = c.execute("SELECT id, domain, username FROM credentials WHERE msg_id=1870 AND LENGTH(username) >= 8 ORDER BY id DESC LIMIT 5").fetchall()
c.close()
print('test rows (rowids):', [r[0] for r in rows])
found = 0
for rid, dom, user in rows:
    needle = user[3:10]
    if len(needle) < 3:
        continue
    t0 = time.time()
    results, total = search(DB, needle, 50, 0)
    dt = time.time() - t0
    ok = any(r.get('id') == rid for r in results)
    print("  %r (user=%s): total=%s dt=%.2fs in-top50=%s" % (needle, user[:18], f"{total:,}", dt, ok))
    if ok:
        found += 1
    time.sleep(0.2)
print('RESULT: %d of %d gap rows found in top-50 FTS hits' % (found, len(rows)))
