import sys
from pathlib import Path
sys.path.insert(0, '/opt/tg-watcher')
import sqlite3
import credentials_db

DB = Path('/opt/tg-watcher/downloads/credentials.db')
NEW_PW = sys.argv[1] if len(sys.argv) > 1 else None
assert NEW_PW, 'usage: setpw.py <newpassword>'

hashed = credentials_db._hash_password(NEW_PW)
conn = sqlite3.connect(str(DB))
n = conn.execute(
    "UPDATE users SET password_hash=? WHERE username='admin'", (hashed,)
).rowcount
conn.commit()
conn.close()
print(f'updated {n} row(s)')
# verify
import credentials_db as c
ok = c.verify_user('admin', NEW_PW, db_path=DB) if hasattr(c, 'verify_user') else 'n/a'
print('verify:', ok)
