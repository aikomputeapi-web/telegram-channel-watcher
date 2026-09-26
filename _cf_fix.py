import json, base64, urllib.request, urllib.error

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

VPS_TUNNEL = '0bd0c623-d436-42c4-a7a0-555d93b5c425'   # cred-search-vps, healthy, linux connector
ZONE_HOST = 'cred-search.nowrouter.store'
ORIGIN = 'http://localhost:8081'

# 1. Push ingress rules to the healthy VPS tunnel (remotely managed: source=cloudflare)
cfg_body = {
    'config': {
        'ingress': [
            {'hostname': ZONE_HOST, 'service': ORIGIN, 'originRequest': {}},
            {'service': 'http_status:404', 'originRequest': {}},
        ],
        'warp-routing': {'enabled': False},
    }
}
r = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel/{VPS_TUNNEL}/configurations',
        method='PUT', body=cfg_body)
print('ingress PUT success:', r['success'])
print(json.dumps(r['result']['config'], indent=1))

# 2. Repoint DNS CNAME to the healthy tunnel
recs = api(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={ZONE_HOST}')['result']
assert len(recs) == 1, recs
rec = recs[0]
upd = {
    'type': 'CNAME',
    'name': ZONE_HOST,
    'content': f'{VPS_TUNNEL}.cfargotunnel.com',
    'proxied': True,
    'ttl': 1,
}
r2 = api(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rec["id"]}',
         method='PUT', body=upd)
print('DNS PUT success:', r2['success'], '->', r2['result']['content'])
