import json, base64, urllib.request, urllib.error

CERT = r'C:\Users\Administrator\.cloudflared\cert.pem'
with open(CERT) as f:
    text = f.read()
token_b64 = ''.join(l for l in text.strip().split('\n') if 'BEGIN' not in l and 'END' not in l)
data = json.loads(base64.b64decode(token_b64).decode())
zone_id = data['zoneID']
acct = data['accountID']
H = {'Authorization': f'Bearer {api_token}' if False else f'Bearer {data["apiToken"]}', 'Content-Type': 'application/json'}

def api(url):
    req = urllib.request.Request(url, headers=H)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

# 1. All tunnels in the account
print('=== ACCOUNT TUNNELS ===')
try:
    tunnels = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel?is_deleted=false')['result']
    for t in tunnels:
        print(f"  {t['id']}  name={t['name']}  status={t['status']}  created={t.get('created_at','?')}")
        try:
            conns = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel/{t["id"]}/connections')['result']
            for c in conns:
                for cc in c.get('conns', []):
                    print(f"      conn: colo={cc.get('colo_name')} origin_ip={c.get('origin_ip')} arch={c.get('arch')} opened={cc.get('opened_at')}")
        except Exception as e:
            print(f'      conns: {e}')
except urllib.error.HTTPError as e:
    print('ERR', e, e.read()[:200])

# 2. DNS records for the zone
print('\n=== DNS RECORDS (nowrouter.store) ===')
recs = api(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?per_page=100')['result']
for r in recs:
    print(f"  {r['type']:6} {r['name']:35} -> {r['content']}  proxied={r.get('proxied')}")
