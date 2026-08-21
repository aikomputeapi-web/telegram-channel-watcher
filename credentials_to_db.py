#!/usr/bin/env python3
"""Load credentials.txt files into SQLite databases.

Usage: credentials_to_db.py <uploads_dir>

For every parsed archive under <uploads_dir> that contains credentials.txt
files, builds <archive>.db with a `credentials` table. Then merges every
archive DB into a single combined credentials_all.db (INSERT OR IGNORE dedups
on the UNIQUE(domain, username, password) constraint).
"""
import sqlite3
import sys
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL,
    source TEXT,
    UNIQUE(domain, username, password)
);
CREATE INDEX IF NOT EXISTS idx_credentials_domain ON credentials(domain);
"""


def load_into(db_path: Path, cred_files, source_label: str) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    rows = []
    for f in cred_files:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                parts = line.split(":", 2)
                if len(parts) < 2:
                    continue
                domain, username = parts[0], parts[1] if len(parts) > 1 else ""
                password = parts[2] if len(parts) > 2 else ""
                rows.append((domain, username, password, source_label))
                if len(rows) >= 5000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO credentials(domain, username, password, source) "
                        "VALUES (?,?,?,?)", rows)
                    rows.clear()
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO credentials(domain, username, password, source) "
            "VALUES (?,?,?,?)", rows)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    conn.close()
    return n


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    base = Path(sys.argv[1])
    combined = base / "credentials_all.db"
    if combined.exists():
        try:
            combined.unlink()
        except PermissionError:
            print(f"WARNING: {combined.name} is locked (open in another app). "
                  f"Writing to {combined.name}.new instead - close the app and rename if desired.")
            combined = base / "credentials_all.db.new"
    combined_conn = sqlite3.connect(str(combined))
    combined_conn.executescript(SCHEMA)
    combined_rows = []

    for archive in sorted(p for p in base.iterdir() if p.is_dir()):
        cred_files = sorted(archive.rglob("credentials.txt"))
        if not cred_files:
            continue
        db_path = base / f"{archive.name}.db"
        if db_path.exists():
            db_path.unlink()
        n = load_into(db_path, cred_files, archive.name)
        print(f"{archive.name}: {n} credentials -> {db_path.name}")

        # merge into combined (stream, dedup via INSERT OR IGNORE)
        for f in cred_files:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    parts = line.split(":", 2)
                    if len(parts) < 2:
                        continue
                    domain, username = parts[0], parts[1] if len(parts) > 1 else ""
                    password = parts[2] if len(parts) > 2 else ""
                    combined_rows.append((domain, username, password, archive.name))
                    if len(combined_rows) >= 5000:
                        combined_conn.executemany(
                            "INSERT OR IGNORE INTO credentials(domain, username, password, source) "
                            "VALUES (?,?,?,?)", combined_rows)
                        combined_rows.clear()
    if combined_rows:
        combined_conn.executemany(
            "INSERT OR IGNORE INTO credentials(domain, username, password, source) "
            "VALUES (?,?,?,?)", combined_rows)
    combined_conn.commit()
    n = combined_conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    combined_conn.close()
    print(f"COMBINED: {n} unique credentials -> {combined.name}")


if __name__ == "__main__":
    main()
