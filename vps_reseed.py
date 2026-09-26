import sys
sys.path.insert(0, '/tmp')
from credentials_db import seed_admin
seed_admin('admin', 'Str0ngS3arch!2026')
print('Admin re-seeded')
import sqlite3
c = sqlite3.connect('/tmp/vps_db_merge2.db')
print(c.execute('SELECT id, username, password_hash FROM users').fetchall())
c.close()
