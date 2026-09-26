import os, sys
sys.path.insert(0, '/opt/tg-watcher')
from dotenv import load_dotenv
load_dotenv('/opt/tg-watcher/.env')
v = os.getenv('TG_SESSION_STRING')
print('env-loaded len:', len(v) if v else None)
from telethon.sessions import StringSession
try:
    s = StringSession(v)
    print('parsed OK, dc:', s.dc_id)
except Exception as e:
    print('parse ERR:', type(e).__name__, e)
