import json, base64, urllib.request, urllib.error

CERT = r'C:\Users\Administrator\.cloudflared\cert.pem'
with open(CERT) as f:
    text = f.read()
token_b64 = ''.join(l for l in text.strip().split('\n') if 'BEGIN' not in l and 'END' not in l)
data = json.loads(base64.b64decode(token_b64).decode())
zone_id = data['zoneID']
acct = data['accountID']
H = {'Authorization': f'Bearer {data["apiToken"]}', 'Content-Type': 'application/json'}

def api(url):
    req = urllib.request.Request(url, headers=H)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

recs = api(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?per_page=100')['result']
for r in recs:
    if r['name'] == 'cred-search.nowrouter.store':
        print('DNS record:')
        print('  created: ', r.get('created_on'))
        print('  modified:', r.get('modified_on'))
        print('  content: ', r['content'])
        print('  id:      ', r['id'])

dead = '48beabcf-3615-4a68-ad60-857ef521487b'
print('\nConnection history of dead tunnel 48beabcf:')
try:
    conns = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel/{dead}/connections')
    print(json.dumps(conns['result'], indent=1)[:1500])
except urllib.error.HTTPError as e:
    print('ERR', e, e.read()[:200])
