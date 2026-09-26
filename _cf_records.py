import json, base64, urllib.request

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

page = 1
while True:
    recs = api(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?per_page=100&page={page}')['result']
    if not recs:
        break
    for r in recs:
        print(f"{r['type']:7} {r['name']:40} -> {r['content']}")
    page += 1
