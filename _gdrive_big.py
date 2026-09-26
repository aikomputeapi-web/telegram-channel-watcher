import subprocess

out = subprocess.run(['rclone', 'ls', '--max-depth', '3', 'gdrive4:tih1pjc2au59t65h3uqca75ivo'],
                     capture_output=True, text=True, timeout=300)
hits = []
for line in out.stdout.splitlines():
    line = line.strip()
    parts = line.split(None, 1)
    if len(parts) == 2:
        size, name = parts
        try:
            sz = int(size)
        except ValueError:
            continue
        if sz > 50 * 1024 * 1024:  # >50MB
            hits.append((sz, name))
hits.sort(reverse=True)
for sz, name in hits[:25]:
    print(f'{sz/1024/1024:8.1f} MB  {name}')
print(f'total >50MB: {len(hits)}')
