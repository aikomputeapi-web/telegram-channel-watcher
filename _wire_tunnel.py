import json, base64, urllib.request

CERT = r'C:\Users\Administrator\.cloudflared\cert.pem'
with open(CERT) as f:
    text = f.read()
token_b64 = ''.join(l for l in text.strip().split('\n') if 'BEGIN' not in l and 'END' not in l)
data = json.loads(base64.b64decode(token_b64).decode())
zone_id = data['zoneID']
acct = data['accountID']
H = {'Authorization': f'Bearer {data["apiToken"]}', 'Content-Type': 'application/json'}

TUNNEL = '0e3eb6e5-3c2d-4c34-b13b-0e13e932a484'

def api(url, method='GET', body=None):
    req = urllib.request.Request(url, headers=H, method=method,
                                 data=json.dumps(body).encode() if body else None)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

# Remote-managed ingress for the new tunnel
cfg_body = {
    'config': {
        'ingress': [
            {'hostname': 'cred-search.nowrouter.store', 'service': 'http://localhost:5000', 'originRequest': {}},
            {'hostname': 'tg-uploads.nowrouter.store', 'service': 'http://localhost:8787', 'originRequest': {}},
            {'service': 'http_status:404', 'originRequest': {}},
        ],
        'warp-routing': {'enabled': False},
    }
}
r = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel/{TUNNEL}/configurations',
        method='PUT', body=cfg_body)
print('ingress PUT:', r['success'])

# Point DNS records at the new tunnel CNAMES
for host in ['cred-search.nowrouter.store', 'tg-uploads.nowrouter.store']:
    recs = api(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={host}')['result']
    upd = {'type': 'CNAME', 'name': host if host.startswith('cred') else 'tg-uploads',
           'content': f'{TUNNEL}.cfargotunnel.com', 'proxied': True, 'ttl': 1}
    # name must be bare hostname portion relative to zone
    upd['name'] = host.split('.')[0]
    if recs:
        r2 = api(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{recs[0]['id']}",
                 method='PUT', body=upd)
        print(f'DNS updated {host}:', r2['success'])
    else:
        r2 = api(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records',
                 method='POST', body=upd)
        print(f'DNS created {host}:', r2['success'])
