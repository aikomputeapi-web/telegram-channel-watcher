import sqlite3, os

p = r"C:\Users\Administrator\Downloads\tg-watcher\uploads\vps_db_merge2.db"
print(f"size: {os.path.getsize(p)/1024/1024:.0f} MB")
c = sqlite3.connect(p)
print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("tables:", tables)
if 'credentials' in tables:
    n = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    print(f"credentials rows: {n:,}")
    print("msg_id range:", c.execute("SELECT MIN(msg_id), MAX(msg_id), COUNT(DISTINCT msg_id) FROM credentials").fetchone())
    print("domains:", c.execute("SELECT COUNT(DISTINCT domain) FROM credentials").fetchone()[0])
    for d in c.execute("SELECT domain, COUNT(*) FROM credentials GROUP BY domain ORDER BY COUNT(*) DESC LIMIT 5"):
        print("  top domain:", d)
if 'users' in tables:
    print("users:", c.execute("SELECT COUNT(*) FROM users").fetchone()[0])
c.close()
