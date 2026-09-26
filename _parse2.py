import os
from dotenv import load_dotenv
load_dotenv('/opt/tg-watcher/.env')
v = os.getenv('TG_SESSION_STRING')
from telethon.sessions import StringSession
s = StringSession(v)
print('parsed OK dc:', s.dc_id)
