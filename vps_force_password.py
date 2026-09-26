import sys
sys.path.insert(0, '/tmp')
from credentials_db import _hash_password, _verify_password
import sqlite3

db = '/tmp/vps_db_merge2.db'
password = 'Str0ngS3arch!2026'

# Force-update the admin password directly
new_hash = _hash_password(password)
c = sqlite3.connect(db)
c.execute("UPDATE users SET password_hash=? WHERE username=?", (new_hash, 'admin'))
c.commit()
c.close()

# Verify
c = sqlite3.connect(db)
stored = c.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
c.close()

print(f"Stored hash: {stored[:30]}...")
print(f"Verify password: {_verify_password(password, stored)}")
