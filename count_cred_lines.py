import os, glob

uploads = r"C:\Users\Administrator\Downloads\tg-watcher\uploads"

# Already ingested msg_ids in vps_db_merge2.db
ingested = {1603, 1631, 1632, 1633, 1634, 1635, 1636, 1637, 1638, 1639, 1640, 1641, 1642, 1643, 1645, 1646, 1647, 1648}

total_lines = 0
for d in sorted(os.listdir(uploads)):
    if not d.endswith('.parsed'):
        continue
    full = os.path.join(uploads, d)
    if not os.path.isdir(full):
        continue
    try:
        msg_id = int(d.split('_')[0])
    except:
        continue

    # Count lines in all credentials.txt files
    cred_files = glob.glob(os.path.join(full, "**", "credentials.txt"), recursive=True)
    dir_lines = 0
    for cf in cred_files:
        with open(cf, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if line.strip():
                    dir_lines += 1
    total_lines += dir_lines
    status = "INGESTED" if msg_id in ingested else "NOT INGESTED"
    print(f"  msg {msg_id:>5} | {dir_lines:>8,} lines | {len(cred_files):>3} files | {status} | {d[:55]}")

print(f"\n=== Total credential lines across all parsed dirs: {total_lines:,} ===")
