import re, sys
txt = open('/opt/tg-watcher/.env').read()
m = re.search(r'TG_SESSION_STRING=(.*)', txt)
v = m.group(1).strip() if m else ''
print('rawlen:', len(m.group(1)) if m else -1)
print('striplen:', len(v))
print('head:', repr(v[:12]))
print('tail:', repr(v[-6:]))
print('has_space:', ' ' in v)
print('b64ok:', end=' ')
import base64
try:
    pad = '=' * (-len(v) % 4)
    d = base64.urlsafe_b64decode(v + pad)
    print(len(d))
except Exception as e:
    print('ERR', e)
