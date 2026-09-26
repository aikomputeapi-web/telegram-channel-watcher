import json, base64, urllib.request

CERT = r'C:\Users\Administrator\.cloudflared\cert.pem'
with open(CERT) as f:
    text = f.read()
token_b64 = ''.join(l for l in text.strip().split('\n') if 'BEGIN' not in l and 'END' not in l)
data = json.loads(base64.b64decode(token_b64).decode())
zone_id = data['zoneID']
acct = data['accountID']
H = {'Authorization': f'Bearer {data["apiToken"]}', 'Content-Type': 'application/json'}

def api(url, method='GET', body=None):
    req = urllib.request.Request(url, headers=H, method=method,
                                 data=json.dumps(body).encode() if body else None)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

# 1. Create a new tunnel for the new VPS
tun = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel',
          method='POST', body={'name': 'tg-newvps', 'config_src': 'cloudflare'})
if tun.get('success'):
    t = tun['result']
    print('TUNNEL_ID=' + t['id'])
    print('TUNNEL_TOKEN=' + t['token'])
else:
    # maybe exists
    lst = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel?is_deleted=false')
    print('EXISTING TUNNELS:')
    for x in lst['result']:
        print(' ', x['id'], x['name'], x['status'])
