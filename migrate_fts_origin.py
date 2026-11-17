"""Migrate the FTS5 index to include the `origin` column (URL paths).

Run on a live DB (WAL mode allows concurrent reads) whenever credentials_fts
predates the origin column. Drops/recreates the FTS table + triggers with the
4-column schema, then indexes in 20k chunks so peak RSS stays small.

    python migrate_fts_origin.py [db_path]

Takes ~12 min on a 7.3M-row / 4.4GB DB. The web UI returns 0 results while the
index is empty, so run it with the service up but expect a gap.
"""
import sqlite3, time
import sys
DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/tg-watcher/downloads/credentials.db"
con = sqlite3.connect(DB)
cur = con.cursor()
cur.executescript("""
DROP TRIGGER IF EXISTS credentials_fts_ai;
DROP TRIGGER IF EXISTS credentials_fts_ad;
DROP TRIGGER IF EXISTS credentials_fts_au;
DROP TABLE IF EXISTS credentials_fts;
""")
con.commit()
print("dropped", flush=True)
cur.executescript("""
CREATE VIRTUAL TABLE credentials_fts USING fts5(
    domain, username, password, origin,
    content='credentials', content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER credentials_fts_ai AFTER INSERT ON credentials BEGIN
    INSERT INTO credentials_fts(rowid, domain, username, password, origin)
    VALUES (new.id, new.domain, new.username, new.password, new.origin);
END;
CREATE TRIGGER credentials_fts_ad AFTER DELETE ON credentials BEGIN
    INSERT INTO credentials_fts(credentials_fts, rowid, domain, username, password, origin)
    VALUES ('delete', old.id, old.domain, old.username, old.password, old.origin);
END;
CREATE TRIGGER credentials_fts_au AFTER UPDATE ON credentials BEGIN
    INSERT INTO credentials_fts(credentials_fts, rowid, domain, username, password, origin)
    VALUES ('delete', old.id, old.domain, old.username, old.password, old.origin);
    INSERT INTO credentials_fts(rowid, domain, username, password, origin)
    VALUES (new.id, new.domain, new.username, new.password, new.origin);
END;
""")
con.commit()
print("recreated", flush=True)
cur.execute("PRAGMA cache_size=-64000")
t0 = time.time(); total = 0; rid_min = 0
while True:
    rows = cur.execute(
        "SELECT id, domain, username, password, origin FROM credentials "
        "WHERE id > ? ORDER BY id LIMIT 20000", (rid_min,)).fetchall()
    if not rows:
        break
    chunk = [(r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or "") for r in rows]
    rid_min = rows[-1][0]
    cur.executemany(
        "INSERT INTO credentials_fts(rowid, domain, username, password, origin) "
        "VALUES (?,?,?,?,?)", chunk)
    con.commit()
    total += len(chunk)
    if total % 500000 < 20000:
        print(f"indexed {total} ({time.time()-t0:.0f}s)", flush=True)
print(f"DONE {total} in {time.time()-t0:.0f}s", flush=True)
for q in ('"github.com/login"', 'origin : "github.com/login"', 'origin : "/login"', '"gmail.com"'):
    print(q, "->", cur.execute("SELECT COUNT(*) FROM credentials_fts WHERE credentials_fts MATCH ?", (q,)).fetchone()[0], flush=True)
con.close()
