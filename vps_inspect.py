import sqlite3, os

db = '/tmp/vps_db_merge2.db'
c = sqlite3.connect(db)

# Schema
schema = c.execute("SELECT sql FROM sqlite_master WHERE name='credentials'").fetchone()
print(f"Schema: {schema[0][:500] if schema else 'NOT FOUND'}")

# Indexes
indexes = c.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='credentials'").fetchall()
print(f"Indexes: {indexes}")

# FTS tables
fts = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'").fetchall()
print(f"FTS tables: {fts}")

# Users table
users = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchall()
print(f"Users table: {users}")

# DB size
print(f"DB file size: {os.path.getsize(db) / 1e6:.1f} MB")

# Row count
print(f"Credentials: {c.execute('SELECT COUNT(*) FROM credentials').fetchone()[0]} rows")

# Sample
print(f"Sample: {c.execute('SELECT id,domain,username,password FROM credentials LIMIT 1').fetchone()}")

# Check if port 5000 was the old setup
print()

# Check credentials_db.py env var on VPS
import re
with open('/tmp/credentials_db.py') as f:
    content = f.read()
m = re.search(r'CREDB[AB]+_DB_PATH', content)
print(f"Env var in credentials_db.py: {m.group(0) if m else 'NOT FOUND'}")
