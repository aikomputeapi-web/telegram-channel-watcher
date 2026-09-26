import json, base64, urllib.request, urllib.error

CERT = r'C:\Users\Administrator\.cloudflared\cert.pem'
with open(CERT) as f:
    text = f.read()
token_b64 = ''.join(l for l in text.strip().split('\n') if 'BEGIN' not in l and 'END' not in l)
data = json.loads(base64.b64decode(token_b64).decode())
acct = data['accountID']
H = {'Authorization': f'Bearer {data["apiToken"]}', 'Content-Type': 'application/json'}

def api(url, method='GET', body=None):
    req = urllib.request.Request(url, headers=H, method=method,
                                 data=json.dumps(body).encode() if body else None)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

tunnel_id = '0bd0c623-d436-42c4-a7a0-555d93b5c425'
print('=== cred-search-vps tunnel CONFIG (ingress) ===')
cfg = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel/{tunnel_id}/configurations')
print(json.dumps(cfg['result'], indent=1))

# Also check the dead nowrouter tunnel's config for comparison
tunnel_id2 = '48beabcf-3615-4a68-ad60-857ef521487b'
print('\n=== nowrouter (dead) tunnel CONFIG ===')
try:
    cfg2 = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel/{tunnel_id2}/configurations')
    print(json.dumps(cfg2['result'], indent=1))
except urllib.error.HTTPError as e:
    print('ERR', e)
