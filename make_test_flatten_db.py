#!/usr/bin/env python3
"""Build a small test DB (prod schema) with real garbled rows for fixer testing."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "prod_snapshot"))
from credentials_db import SCHEMA, FTS_SCHEMA  # noqa: E402

db = Path(__file__).parent / "test_flatten.db"
if db.exists():
    db.unlink()
for suffix in ("-wal", "-shm"):
    p = Path(str(db) + suffix)
    if p.exists():
        p.unlink()

conn = sqlite3.connect(str(db))
conn.executescript(SCHEMA)
conn.executescript(FTS_SCHEMA)
# Mirror prod: the unique dedup index is added by a migration on prod
# (not part of the SCHEMA constant). Required to test INSERT OR IGNORE dedup.
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cred_dedup "
    "ON credentials(domain, username, password)"
)

T = "\t"
rows = [
    # (msg_id, domain, username, password, origin, browser, source_file, record_id, source_dir, part)
    # 1. Real garbled row (db id 9044790): UVC origin, extras in password
    (2262, "unlimitedvacationclub.com", "chitodeleon@gmail.com",
     f"9058241314{T}https://www.costco.ca/RegisterView{T}pidiongpana@yahoo.com{T}MIT77abe#{T}https://global.americanexpress.com/myca/logon/canlac/action{T}sn*****{T}mitbs77",
     "https://unlimitedvacationclub.com/", "", "passwords.tsv",
     "unlimitedvacationclub.com:chitodeleon@gmail.com:9058241314", "2262_parsed", "part-002.003"),
    # 2. Real garbled row (db id 9044967): triple distributed across fields
    (2262, "amazon.com",
     f"https://www.costco.ca/RegisterView{T}pidiongpana@yahoo.com{T}MIT77abe#",
     f"https://global.americanexpress.com/myca/logon/canlac/action{T}sn*****{T}mitbs77",
     f"https://unlimitedvacationclub.com/{T}chitodeleon@gmail.com{T}9058241314",
     "brave, profile: 0", "passwords.txt",
     "amazon.com:https://www.costco.ca/RegisterView:https://global.americanexpress.com", "2262_parsed", "part-002.003"),
    # 3. A clean row that already exists (trios from #1 should dedup against equivalent clean data)
    (2262, "www.costco.ca", "pidiongpana@yahoo.com", "MIT77abe#",
     "https://www.costco.ca/RegisterView", "", "passwords.tsv",
     "www.costco.ca:pidiongpana@yahoo.com:MIT77abe#", "2262_parsed", "part-002.003"),
    # 4. Ragged row: tabs + urls but not clean trios -> must be kept untouched
    (2262, "example.com", f"user{T}https://weird.example/path{T}notatrio",
     f"pw{T}tail", "https://example.com/", "", "passwords.tsv",
     "example.com:user:pw", "2262_parsed", "part-002.003"),
    # 5. Normal tab-free rows
    (2262, "unlimitedvacationclub.com", "chitodeleon@gmail.com", "CDLbs#0314",
     "https://www.unlimitedvacationclub.com/ChangePassword", "", "passwords.tsv",
     "unlimitedvacationclub.com:chitodeleon@gmail.com:CDLbs#0314", "2262_parsed", "part-002.003"),
    (2262, "tumblr.com", "pidiongpana@yahoo.com", "CDLbs#0314",
     "https://www.tumblr.com/login", "", "passwords.tsv",
     "tumblr.com:pidiongpana@yahoo.com:CDLbs#0314", "2262_parsed", "part-002.003"),
]

conn.executemany(
    """INSERT INTO credentials
       (msg_id, domain, username, password, origin, browser, source_file,
        record_id, source_dir, part)
       VALUES (?,?,?,?,?,?,?,?,?,?)""",
    rows,
)
conn.commit()
n = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
print(f"seeded {n} rows into {db}")
conn.close()
