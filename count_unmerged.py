import sqlite3, os, json, glob

uploads = r"C:\Users\Administrator\Downloads\tg-watcher\uploads"

# Check which msg_ids are already in the VPS DB
vps_db = os.path.join(uploads, "vps_db_merge2.db")
c = sqlite3.connect(vps_db)
ingested_msg_ids = set(r[0] for r in c.execute("SELECT DISTINCT msg_id FROM credentials").fetchall())
c.close()
print(f"Already ingested msg_ids: {sorted(ingested_msg_ids)}")
print()

# Find all parsed directories and count their credentials
total_unmerged = 0
for d in sorted(os.listdir(uploads)):
    if not d.endswith('.parsed'):
        continue
    full = os.path.join(uploads, d)
    if not os.path.isdir(full):
        continue
    
    # Extract msg_id from directory name
    try:
        msg_id = int(d.split('_')[0])
    except:
        continue
    
    # Find records.jsonl files and count credential records
    cred_count = 0
    jsonl_files = glob.glob(os.path.join(full, "**", "records.jsonl"), recursive=True)
    for jf in jsonl_files:
        with open(jf, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("artifact_type") == "credential":
                        cred_count += 1
                except:
                    pass
    
    status = "INGESTED" if msg_id in ingested_msg_ids else "NOT INGESTED"
    if msg_id not in ingested_msg_ids:
        total_unmerged += cred_count
    print(f"  msg {msg_id:>5} | {cred_count:>8,} creds | {status} | {d[:60]}")

print(f"\n=== Total unmerged credentials: {total_unmerged:,} ===")
