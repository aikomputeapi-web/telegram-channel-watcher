import json, base64, urllib.request, urllib.error, time, ssl

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

VPS_TUNNEL = '0bd0c623-d436-42c4-a7a0-555d93b5c425'
conns = api(f'https://api.cloudflare.com/client/v4/accounts/{acct}/cfd_tunnel/{VPS_TUNNEL}/connections')['result']
print('=== cred-search-vps current connections ===')
n = 0
for c in conns:
    for cc in c.get('conns', []):
        n += 1
        print(f"  colo={cc.get('colo_name')} arch={c.get('arch')} ver={c.get('version')} features={c.get('features')}")
print('total conns:', n)

# poll the site
ctx = ssl.create_default_context()
for i in range(8):
    try:
        req = urllib.request.Request(f'https://cred-search.nowrouter.store/?cb={time.time()}',
                                     headers={'User-Agent': 'curl/8'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f'poll {i}: HTTP {resp.status}')
            body = resp.read(200)
            print('  body sample:', body[:120])
            break
    except urllib.error.HTTPError as e:
        print(f'poll {i}: HTTP {e.code}')
    except Exception as e:
        print(f'poll {i}: {e}')
    time.sleep(8)
