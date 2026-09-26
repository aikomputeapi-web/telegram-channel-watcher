import json, base64, urllib.request, urllib.error

CERT = r'C:\Users\Administrator\.cloudflared\cert.pem'
with open(CERT) as f:
    text = f.read()
token_b64 = ''.join(l for l in text.strip().split('\n') if 'BEGIN' not in l and 'END' not in l)
data = json.loads(base64.b64decode(token_b64).decode())
acct = data['accountID']
H = {'Authorization': f'Bearer {data["apiToken"]}', 'Content-Type': 'application/json'}

def api(url):
    req = urllib.request.Request(url, headers=H)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

try:
    logs = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/audit_logs?per_page=50&direction=desc')
    for it in logs['result']:
        when = it.get('when', '?')[:19]
        actor = (it.get('actor', {}) or {}).get('email', '?')
        action = it.get('action', {}).get('type', '?')
        res = (it.get('resource', {}) or {}).get('type', '')
        print(f'{when}  {actor:35} {action:25} {res}')
except urllib.error.HTTPError as e:
    print('audit logs not permitted for this token:', e.code)
