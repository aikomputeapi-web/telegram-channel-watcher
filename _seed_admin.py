import sys
from pathlib import Path
sys.path.insert(0, '/opt/tg-watcher')
from credentials_db import seed_admin
DB = Path('/opt/tg-watcher/downloads/credentials.db')
seed_admin('admin', 'Dirty2020!Passwordcameron123', db_path=DB)
print('password updated')
