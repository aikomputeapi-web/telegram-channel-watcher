import os, sqlite3, glob

patterns = ['/tmp/*.db', '/tmp/*.sqlite', '/tmp/*.sqlite3', '/home/**/*.db', '/home/**/*.sqlite', '/home/**/*.sqlite3']
files = set()
for p in patterns:
    files.update(glob.glob(p, recursive=True))

for f in sorted(files):
    try:
        c = sqlite3.connect(f)
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        size_mb = os.path.getsize(f) / 1024 / 1024
        if 'credentials' in tables:
            rows = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
            print(f"{f}  ({size_mb:.1f}MB, {rows} rows)")
        elif tables:
            print(f"{f}  ({size_mb:.1f}MB, tables: {tables})")
        else:
            print(f"{f}  ({size_mb:.1f}MB, empty/other)")
        c.close()
    except Exception as e:
        print(f"{f}  ERROR: {e}")
