import sqlite3

conn = sqlite3.connect('/tmp/vps_db_merge2.db')
# Check if FTS5 table exists
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'").fetchall()
print('FTS tables:', tables)
# Count rows
total = conn.execute('SELECT COUNT(*) FROM credentials').fetchone()[0]
print('Total credentials:', total)
# Test FTS5 search
try:
    r = conn.execute('SELECT COUNT(*) FROM credentials_fts WHERE credentials_fts MATCH ?', ('"gmail"',)).fetchone()[0]
    print('FTS5 gmail results:', r)
except Exception as e:
    print('FTS5 error:', e)
conn.close()
