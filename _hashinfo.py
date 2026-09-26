import sys
from pathlib import Path
sys.path.insert(0, '/opt/tg-watcher')
import credentials_db
import inspect
src = inspect.getsource(credentials_db._hash_password)
print(src)
