import json, base64, subprocess, urllib.request, urllib.error

# Read the cert.pem and decode the token
with open('/tmp/cert.pem') as f:
    lines = f.read().strip().split('\n')
    token_lines = [l for l in lines if 'BEGIN' not in l and 'END' not in l]
    token_b64 = ''.join(token_lines)
    token_json = base64.b64decode(token_b64).decode()
    data = json.loads(token_json)

zone_id = data['zoneID']
api_token = data['apiToken']

# List DNS records for cred-search
url = f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name=cred-search.nowrouter.store'
req = urllib.request.Request(url, headers={
    'Authorization': f'Bearer {api_token}',
    'Content-Type': 'application/json'
})
resp = urllib.request.urlopen(req)
records = json.loads(resp.read())['result']
print(f'Found {len(records)} DNS records for cred-search.nowrouter.store:')
for r in records:
    print(f"  ID: {r['id']} Type: {r['type']} Content: {r['content']}")

# Delete each record
for r in records:
    del_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{r['id']}"
    del_req = urllib.request.Request(del_url, method='DELETE', headers={
        'Authorization': f'Bearer {api_token}',
        'Content-Type': 'application/json'
    })
    try:
        del_resp = urllib.request.urlopen(del_req)
        print(f"Deleted record {r['id']}: {del_resp.status}")
    except urllib.error.HTTPError as e:
        print(f"Failed to delete {r['id']}: {e}")

print("Now recreating CNAME for the nowrouter tunnel...")
