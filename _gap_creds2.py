import os, glob

base = r"C:\Users\Administrator\Downloads\tg-watcher\uploads"
total = 0
for d in sorted(os.listdir(base)):
    full = os.path.join(base, d)
    if not os.path.isdir(full) or not d.endswith('.parsed'):
        continue
    try:
        msg_id = int(d.split('_')[0])
    except ValueError:
        continue
    n = 0
    for cf in glob.glob(os.path.join(full, "**", "credentials.txt"), recursive=True):
        try:
            with open(cf, 'r', encoding='utf-8', errors='replace') as fh:
                n += sum(1 for line in fh if line.strip())
        except OSError:
            pass
    total += n
    print(f"msg {msg_id:>5} | {n:>8,} creds | {d[:70]}")

print(f"\nTOTAL: {total:,}")
