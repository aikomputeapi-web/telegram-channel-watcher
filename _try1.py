from telethon.sessions import StringSession

v = open('/opt/tg-watcher/.env').read().split('TG_SESSION_STRING=')[1].split()[0]
print('first char:', repr(v[0]), 'len:', len(v))
try:
    s = StringSession('1' + v)
    print('with-1-prefix dc:', s.dc_id, 'port:', s.port, 'addr:', s.server_address)
except Exception as e:
    print('with-1 ERR:', type(e).__name__, e)
