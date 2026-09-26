import sys, os
sys.path.insert(0, '/tmp')
from credentials_db import _hash_password, _verify_password
import sqlite3

db = '/tmp/vps_db_merge2.db'
password = 'Dirty2020!'

new_hash = _hash_password(password)
c = sqlite3.connect(db)
c.execute("UPDATE users SET password_hash=? WHERE username=?", (new_hash, 'admin'))
c.commit()
stored = c.execute("SELECT password_hash FROM users WHERE username=?", ('admin',)).fetchone()[0]
c.close()
print(f"Password set. Verify: {_verify_password(password, stored)}")
