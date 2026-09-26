import os, json, glob

base = r"C:\Users\Administrator\Downloads\tg-watcher\uploads"
out = []
total_creds = 0
for d in sorted(os.listdir(base)):
    full = os.path.join(base, d)
    if not os.path.isdir(full) or not d.endswith('.parsed'):
        continue
    # msg_id = leading number in dir name
    try:
        msg_id = int(d.split('_')[0])
    except ValueError:
        continue
    creds = 0
    for jf in glob.glob(os.path.join(full, "**", "records.jsonl"), recursive=True):
        with open(jf, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if '"credential"' not in line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    if json.loads(line).get("artifact_type") == "credential":
                        creds += 1
                except Exception:
                    pass
    total_creds += creds
    print(f"msg {msg_id:>5} | {creds:>8,} creds | {d[:70]}")

print(f"\nTOTAL creds in raw records.jsonl: {total_creds:,}")
