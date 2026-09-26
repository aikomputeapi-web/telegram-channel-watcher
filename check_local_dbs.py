import sqlite3, glob, os

patterns = [
    r"C:\Users\Administrator\Downloads\tg-watcher\uploads\*.db",
]
total_rows = 0
for p in patterns:
    for f in sorted(glob.glob(p)):
        try:
            sz = os.path.getsize(f)
            c = sqlite3.connect(f)
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if 'credentials' in tables:
                rows = c.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
                total_rows += rows
                print(f"{f}  ({sz/1024/1024:.1f}MB, {rows:,} rows)")
            else:
                print(f"{f}  ({sz/1024/1024:.1f}MB, tables: {tables})")
            c.close()
        except Exception as e:
            print(f"{f}  ERROR: {e}")

print(f"\n=== Total credential rows: {total_rows:,} ===")
