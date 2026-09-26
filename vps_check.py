import sqlite3, sys
sys.path.insert(0, '/tmp')
c = sqlite3.connect('/tmp/vps_db_merge2.db')
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('Tables:', tables)
