import os, shutil
# Update cloudflared config to add cred-search route
config_path = '/etc/cloudflared/config.yml'
new_config = """tunnel: 48beabcf-3615-4a68-ad60-857ef521487b
credentials-file: /etc/cloudflared/48beabcf-3615-4a68-ad60-857ef521487b.json
ingress:
  - hostname: cred-search.nowrouter.store
    service: http://localhost:8081
  - hostname: nowrouter.info
    service: http://localhost:8080
  - service: http_status:404
"""

# Backup original
shutil.copy2(config_path, config_path + '.bak')
with open(config_path, 'w') as f:
    f.write(new_config)
print("Updated cloudflared config:")
with open(config_path) as f:
    print(f.read())
