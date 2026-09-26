import subprocess

remotes = ['gdrive1', 'gdrive2', 'gdrive3', 'gdrive5', 'gdrive6', 'gdrive7', 'gdrive8', 'gdrive9', 'gdrive10']
for r in remotes:
    try:
        out = subprocess.run(['rclone', 'ls', '--max-depth', '4', f'{r}:'],
                             capture_output=True, text=True, timeout=120)
        hits = []
        for line in out.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                try:
                    sz = int(parts[0])
                except ValueError:
                    continue
                low = parts[1].lower()
                if ('credential' in low or ('db' in low and sz > 20*1024*1024) or 'tg-watcher' in low):
                    hits.append((sz, parts[1]))
        if hits:
            print(f'=== {r} ===')
            for sz, name in sorted(hits, reverse=True)[:10]:
                print(f'  {sz/1024/1024:8.1f} MB  {name}')
        else:
            print(f'=== {r} ===  (no matches)')
    except Exception as e:
        print(f'=== {r} ===  ERR {e}')
