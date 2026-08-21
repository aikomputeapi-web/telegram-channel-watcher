"""SQLite credential database for the telegram-channel-watcher.

Records from records.jsonl are ingested into a persistent SQLite DB before
the parsed output dirs are cleaned up. Only credential records are stored
(artifact_type == 'credential'), keeping the DB small and focused.

Schema:
  credentials(id, msg_id, domain, username, password, origin, browser,
              source_file, source_sha256, confidence, duplicate_count,
              family, record_id, imported_at)

The DB lives at downloads/credentials.db on the VPS and is queryable via
SQL, the CLI helper, or the Flask web UI (search_server.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("credentials_db")

DB_PATH = Path(os.getenv("CREDBB_DB_PATH", "downloads/credentials.db"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id          INTEGER NOT NULL,
    domain          TEXT NOT NULL,
    username        TEXT,
    password        TEXT NOT NULL,
    origin          TEXT,
    browser         TEXT,
    source_file     TEXT,
    source_sha256   TEXT,
    confidence      TEXT,
    duplicate_count INTEGER DEFAULT 1,
    family          TEXT,
    record_id       TEXT UNIQUE,
    imported_at     TEXT DEFAULT (datetime('now')),
    source_dir      TEXT,
    part            TEXT
);
CREATE INDEX IF NOT EXISTS idx_cred_domain   ON credentials(domain);
CREATE INDEX IF NOT EXISTS idx_cred_username ON credentials(username);
CREATE INDEX IF NOT EXISTS idx_cred_msg_id   ON credentials(msg_id);
CREATE INDEX IF NOT EXISTS idx_cred_origin   ON credentials(origin);
CREATE INDEX IF NOT EXISTS idx_cred_password ON credentials(password);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    approved      INTEGER DEFAULT 0,
    is_admin      INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""


def init_db(db_path: Path | None = None) -> None:
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    log.info("DB initialized at %s", db_path)


def _hostname(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if "://" not in text:
        text = "//" + text
    try:
        host = urlparse(text).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().lstrip(".").lstrip("www.")
    if any(ch.isspace() or ch in "/\\:?#@[]" for ch in host):
        return None
    return host or None


def ingest_records(msg_id: int, output_dir: Path, db_path: Path | None = None) -> int:
    """Read all records.jsonl under output_dir, insert credential records
    into the DB. Returns the number of rows inserted."""
    db_path = db_path or DB_PATH
    init_db(db_path)
    inserted = 0
    seen_ids: set[str] = set()

    records_files = sorted(output_dir.rglob("records.jsonl"))
    if not records_files:
        log.warning("msg %d: no records.jsonl found under %s", msg_id, output_dir)
        return 0

    rows = []
    for rf in records_files:
        part_name = rf.parent.name
        try:
            with rf.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("artifact_type") != "credential":
                        continue
                    rid = rec.get("record_id")
                    if rid and rid in seen_ids:
                        continue
                    if rid:
                        seen_ids.add(rid)
                    payload = rec.get("payload") or {}
                    password = (payload.get("password") or "").strip()
                    username = (payload.get("username") or "").strip()
                    if not password:
                        continue
                    if "\n" in username or "\r" in username or "\n" in password or "\r" in password:
                        continue
                    origin = payload.get("origin") or ""
                    host = _hostname(origin)
                    if not host:
                        continue
                    browser = payload.get("browser") or ""
                    sources = rec.get("sources") or [{}]
                    src_file = sources[0].get("relative_path") or "" if sources else ""
                    src_sha = sources[0].get("source_sha256") or "" if sources else ""
                    rows.append((
                        msg_id,
                        host,
                        username,
                        password,
                        origin,
                        browser,
                        src_file,
                        src_sha,
                        rec.get("confidence", ""),
                        int(rec.get("duplicate_count", 1)),
                        rec.get("family", ""),
                        rid or f"{host}:{username}:{password}",
                        output_dir.name,
                        part_name,
                    ))
        except OSError as exc:
            log.warning("cannot read %s: %s", rf, exc)

    if not rows:
        log.info("msg %d: no credentials to ingest", msg_id)
        return 0

    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO credentials
               (msg_id, domain, username, password, origin, browser,
                source_file, source_sha256, confidence, duplicate_count,
                family, record_id, source_dir, part)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        inserted = conn.total_changes

    log.info("msg %d: ingested %d credentials from %s", msg_id, len(rows), output_dir.name)
    return len(rows)


def search(db_path: Path | None, query: str, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    """Search credentials by domain, username, password, or origin.
    Returns (results, total_count)."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return [], 0
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        pattern = f"%{query}%"
        count_row = conn.execute(
            """SELECT COUNT(*) FROM credentials
               WHERE domain LIKE ? OR username LIKE ? OR password LIKE ?
                  OR origin LIKE ?""",
            (pattern, pattern, pattern, pattern),
        ).fetchone()
        total = count_row[0] if count_row else 0
        rows = conn.execute(
            """SELECT * FROM credentials
               WHERE domain LIKE ? OR username LIKE ? OR password LIKE ?
                  OR origin LIKE ?
               ORDER BY imported_at DESC, id DESC
               LIMIT ? OFFSET ?""",
            (pattern, pattern, pattern, pattern, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows], total


def stats(db_path: Path | None = None) -> dict:
    """Return summary statistics about the DB."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return {"total": 0, "domains": 0, "messages": 0}
    with sqlite3.connect(str(db_path)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
        domains = conn.execute("SELECT COUNT(DISTINCT domain) FROM credentials").fetchone()[0]
        messages = conn.execute("SELECT COUNT(DISTINCT msg_id) FROM credentials").fetchone()[0]
    return {"total": total, "domains": domains, "messages": messages}


# ── user auth ───────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${pwd_hash}"


def _verify_password(password: str, stored: str) -> bool:
    salt, pwd_hash = stored.split("$", 1)
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex() == pwd_hash


def seed_admin(username: str, password: str, db_path: Path | None = None) -> None:
    db_path = db_path or DB_PATH
    init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO users (username, password_hash, approved, is_admin) VALUES (?,?,1,1)",
            (username, _hash_password(password)),
        )
        conn.commit()
    log.info("admin user %s seeded", username)


def create_user(username: str, password: str, db_path: Path | None = None) -> bool:
    db_path = db_path or DB_PATH
    init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?,?)",
            (username, _hash_password(password)),
        )
        conn.commit()
    return True


def authenticate(username: str, password: str, db_path: Path | None = None) -> dict | None:
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return None
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE username=? AND approved=1", (username,)
        ).fetchone()
    if not row:
        return None
    user = dict(row)
    if not _verify_password(password, user["password_hash"]):
        return None
    return user


def list_pending_users(db_path: Path | None = None) -> list[dict]:
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM users WHERE approved=0 AND is_admin=0 ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def approve_user(user_id: int, db_path: Path | None = None) -> None:
    db_path = db_path or DB_PATH
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE users SET approved=1 WHERE id=?", (user_id,))
        conn.commit()


def reject_user(user_id: int, db_path: Path | None = None) -> None:
    db_path = db_path or DB_PATH
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
