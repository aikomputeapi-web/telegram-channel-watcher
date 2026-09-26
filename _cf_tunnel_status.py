import json, base64, urllib.request, urllib.error, sys

CERT = r'C:\Users\Administrator\.cloudflared\cert.pem'

with open(CERT) as f:
    text = f.read()
token_b64 = ''.join(
    l for l in text.strip().split('\n')
    if 'BEGIN' not in l and 'END' not in l
)
data = json.loads(base64.b64decode(token_b64).decode())
zone_id = data['zoneID']
api_token = data['apiToken']

H = {'Authorization': f'Bearer {api_token}', 'Content-Type': 'application/json'}

def api(url):
    req = urllib.request.Request(url, headers=H)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

# Tunnel info (by tunnel ID from config.yml)
tunnel_id = '74af6eec-ea5e-40a0-bb6c-f2d8bca99b9e'
try:
    t = api(f'https://api.cloudflare.com/client/v4/accounts/{data["accountID"]}/cfd_tunnel/{tunnel_id}')
    r = t['result']
    print('TUNNEL:', r.get('name'), '| status:', r.get('status'), '| conns active:', r.get('tun_type'), '| deleted:', r.get('deleted_at'))
    conns = api(f'https://api.cloudflare.com/client/v4/accounts/{data["accountID"]}/cfd_tunnel/{tunnel_id}/connections')
    print('CONNECTIONS:', json.dumps(conns['result'], indent=1)[:500])
except urllib.error.HTTPError as e:
    print('tunnel api error:', e, e.read()[:300])

# Disk-free check via Cloudflare DNS list not needed; just print OK
print('OK')
