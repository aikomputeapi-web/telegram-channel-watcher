import sqlite3

# Check schema of each DB to ensure compatibility
dbs = [
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\vps_db_merge2.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\vps_db_merge.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\credentials_all.db",
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\1631__UP_DAISYCLOUD_CHAMPIONING__14_AUGUST_5600_ON_CHANNEL.parsed.db",
]

for db in dbs:
    c = sqlite3.connect(db)
    schema = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='credentials'").fetchone()
    print(f"\n=== {db.split(chr(92))[-1]} ===")
    print(schema[0] if schema else "(no credentials table)")
    # Check for duplicates - sample a few domains
    sample = c.execute("SELECT domain, username, password FROM credentials LIMIT 3").fetchall()
    for s in sample:
        print(f"  sample: {s[0]} | {s[1]} | {str(s[2])[:20]}")
    # Check if msg_id column exists
    cols = [r[1] for r in c.execute("PRAGMA table_info(credentials)").fetchall()]
    print(f"  columns: {cols}")
    c.close()
