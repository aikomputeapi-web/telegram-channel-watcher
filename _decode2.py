# Try full 352-length variant: v1 strings are 352 chars ip4, 367 ip6.
# Our len 337 string starting 'w' is NOT a telethon StringSession in v1 format.
# Check alternatives: pyrogram session? telethonthon? Try decoding as standard b64 (not urlsafe):
import base64, struct, ipaddress

v = open('/opt/tg-watcher/.env').read().split('TG_SESSION_STRING=')[1].split()[0]

def try_decode(name, s):
    try:
        pad = '=' * (-len(s) % 4)
        if '-' in s or '_' in s:
            d = base64.urlsafe_b64decode(s + pad)
        else:
            d = base64.b64decode(s + pad)
        print(f'{name}: decoded {len(d)} bytes, head={d[:4].hex()}')
        return d
    except Exception as e:
        print(f'{name}: ERR {e}')
        return None

d = try_decode('as-is', v)
# strip first char then decode
d2 = try_decode('strip-first', v[1:])
if d2 and len(d2) >= 265:
    dc_id, ip, port, key = struct.unpack('>B4sH256s', d2[:265])
    print('strip-first parse: dc=', dc_id, 'ip=', ipaddress.ip_address(ip), 'port=', port)
