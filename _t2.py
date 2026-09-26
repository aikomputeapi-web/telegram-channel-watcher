import base64, struct, ipaddress

v = open('/opt/tg-watcher/.env').read().split('TG_SESSION_STRING=')[1].split()[0]
body = base64.urlsafe_b64decode(v + '=' * (-len(v) % 4))
print('total decoded:', len(body), 'first byte:', body[0], 'hex head:', body[:8].hex())

# Try as GramJS-style: b64 of ~ [4B dc][16B ip][4B port][256B key]? that's 280. no.
# Try: [1B dc][4B ip][2B port][245B key] -> 252! 256-11=245. key truncated?
# Or Telethon v2? install and test:
import subprocess, sys
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--target', '/tmp/t2lib', 'telethon==2.0.0a2'], check=False)
sys.path.insert(0, '/tmp/t2lib')
try:
    from telethon.sessions import StringSession as S2
    s2 = S2(v)
    print('TELETHON v2 PARSE OK dc:', s2.dc_id)
except Exception as e:
    print('telethon2 ERR:', type(e).__name__, str(e)[:100])
