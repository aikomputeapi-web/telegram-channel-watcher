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
import re
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("credentials_db")

DB_PATH = Path(os.getenv("CREDDB_DB_PATH", "downloads/credentials.db"))


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply performance pragmas for faster reads + concurrent write/read."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA mmap_size=268435456")


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
    CREATE INDEX IF NOT EXISTS idx_cred_password ON credentials(password);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    approved      INTEGER DEFAULT 0,
    is_admin      INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now')),
    last_login    TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- Audit log of every search executed by approved users. Lets the admin
-- review exactly what each approved account searched and when. Safe to
-- add to an existing DB: CREATE TABLE IF NOT EXISTS is a no-op if present.
CREATE TABLE IF NOT EXISTS search_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    username    TEXT NOT NULL,
    query       TEXT NOT NULL,
    result_count INTEGER DEFAULT 0,
    searched_at TEXT DEFAULT (datetime('now')),
    ip_address  TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_search_log_user    ON search_log(user_id);
CREATE INDEX IF NOT EXISTS idx_search_log_time    ON search_log(searched_at);
CREATE INDEX IF NOT EXISTS idx_search_log_query   ON search_log(query);
CREATE INDEX IF NOT EXISTS idx_search_log_username ON search_log(username);
"""

# FTS5 virtual table with trigram tokenizer for fast substring search.
# Covers domain, username, password, and origin (origin is what carries the
# URL path, so "github.com/login" is searchable as one substring).
# Triggers keep the FTS index in sync on INSERT / UPDATE / DELETE.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS credentials_fts USING fts5(
    domain, username, password, origin,
    content='credentials', content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS credentials_fts_ai AFTER INSERT ON credentials BEGIN
    INSERT INTO credentials_fts(rowid, domain, username, password, origin)
    VALUES (new.id, new.domain, new.username, new.password, new.origin);
END;
CREATE TRIGGER IF NOT EXISTS credentials_fts_ad AFTER DELETE ON credentials BEGIN
    INSERT INTO credentials_fts(credentials_fts, rowid, domain, username, password, origin)
    VALUES ('delete', old.id, old.domain, old.username, old.password, old.origin);
END;
CREATE TRIGGER IF NOT EXISTS credentials_fts_au AFTER UPDATE ON credentials BEGIN
    INSERT INTO credentials_fts(credentials_fts, rowid, domain, username, password, origin)
    VALUES ('delete', old.id, old.domain, old.username, old.password, old.origin);
    INSERT INTO credentials_fts(rowid, domain, username, password, origin)
    VALUES (new.id, new.domain, new.username, new.password, new.origin);
END;
"""

# Columns expected in the FTS index. Older DBs indexed only the first three;
# a missing column means the index must be dropped and rebuilt with origin.
FTS_COLUMNS = ("domain", "username", "password", "origin")


def _fts_needs_rebuild(conn: sqlite3.Connection) -> bool:
    """True if the FTS table exists but lacks any of FTS_COLUMNS (old schema)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(credentials_fts)")}
    if not cols:
        return True
    return bool(set(FTS_COLUMNS) - cols)


def _drop_fts(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS credentials_fts_ai;
        DROP TRIGGER IF EXISTS credentials_fts_ad;
        DROP TRIGGER IF EXISTS credentials_fts_au;
        DROP TABLE IF EXISTS credentials_fts;
        """
    )



def init_db(db_path: Path | None = None, skip_large_rebuild: bool = True) -> None:
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        _apply_pragmas(conn)
        conn.executescript(SCHEMA)
        # Migration: older DBs created the users table without last_login.
        # Add it if missing so the admin user list can show last login time.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if "last_login" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
            conn.commit()
            log.info("Migrated users table: added last_login column")
        # Migration: older FTS schemas indexed only domain/username/password, so
        # URL paths (stored in `origin`) were unsearchable. Drop the stale table
        # and recreate it with the origin column; the caller rebuilds the index.
        stale_fts = _fts_needs_rebuild(conn)
        if stale_fts:
            _drop_fts(conn)
            conn.commit()
            log.info("Dropped stale FTS index (schema upgrade: +origin column)")
        conn.executescript(FTS_SCHEMA)
        # For external content FTS5 tables, COUNT(*) returns the source row count
        # (not the actual index size), so we check the shadow idx table instead.
        # If the inverted index is empty but credentials has rows, rebuild the FTS
        # index in one shot. This handles both fresh DBs and migrating existing VPS DBs.
        #
        # HOWEVER: the rebuild is a single blocking transaction over the whole table.
        # On a multi-million-row production DB this takes minutes and blocks Flask
        # from binding its port on startup, leaving the app unresponsive. When
        # skip_large_rebuild is True (default) we skip the expensive rebuild for big
        # DBs and let the operator run `rebuild_fts()` separately / in the background.
        idx_count = conn.execute("SELECT COUNT(*) FROM credentials_fts_idx").fetchone()[
            0
        ]
        cred_count = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
        if idx_count == 0 and cred_count > 0:
            if skip_large_rebuild and cred_count > 100_000:
                log.warning(
                    "FTS index is empty for %d credentials; skipping blocking "
                    "rebuild on startup to avoid stalling the web server. Run "
                    "`rebuild_fts(%s)` in the background to populate the index.",
                    cred_count,
                    db_path,
                )
            else:
                conn.execute(
                    "INSERT INTO credentials_fts(credentials_fts) VALUES('rebuild')"
                )
        conn.commit()
    log.info("DB initialized at %s", db_path)


def rebuild_fts(db_path: Path | None = None) -> int:
    """Rebuild the FTS5 trigram index in one shot.

    Use this to (re)populate the search index on a large DB where init_db skipped
    the blocking rebuild. Returns the new inverted-index row count. Safe to run
    while the web server is up (WAL mode allows concurrent reads)."""
    db_path = db_path or DB_PATH
    with sqlite3.connect(str(db_path)) as conn:
        _apply_pragmas(conn)
        conn.execute("INSERT INTO credentials_fts(credentials_fts) VALUES('rebuild')")
        conn.commit()
        idx_count = conn.execute("SELECT COUNT(*) FROM credentials_fts_idx").fetchone()[
            0
        ]
    log.info("FTS index rebuilt at %s: %d entries", db_path, idx_count)
    return idx_count


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
    host = host.lower()
    if host.startswith("."):
        host = host.lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if any(ch.isspace() or ch in "/\\:?#@[]" for ch in host):
        return None
    return host or None


def ingest_records(msg_id: int, output_dir: Path, db_path: Path | None = None) -> int:
    """Read all records.jsonl under output_dir, insert credential records
    into the DB. Returns the number of rows inserted."""
    db_path = db_path or DB_PATH
    init_db(db_path)
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
                    if (
                        "\n" in username
                        or "\r" in username
                        or "\n" in password
                        or "\r" in password
                    ):
                        continue
                    origin = payload.get("origin") or ""
                    host = _hostname(origin)
                    if not host:
                        continue
                    browser = payload.get("browser") or ""
                    sources = rec.get("sources") or [{}]
                    src_file = sources[0].get("relative_path") or "" if sources else ""
                    src_sha = sources[0].get("source_sha256") or "" if sources else ""
                    rows.append(
                        (
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
                        )
                    )
        except OSError as exc:
            log.warning("cannot read %s: %s", rf, exc)

    if not rows:
        log.info("msg %d: no credentials to ingest", msg_id)
        return 0

    with sqlite3.connect(str(db_path)) as conn:
        _apply_pragmas(conn)
        conn.executemany(
            """INSERT OR IGNORE INTO credentials
               (msg_id, domain, username, password, origin, browser,
                source_file, source_sha256, confidence, duplicate_count,
                family, record_id, source_dir, part)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()

    log.info(
        "msg %d: ingested %d credentials from %s", msg_id, len(rows), output_dir.name
    )
    return len(rows)


def search(
    db_path: Path | None,
    query: str,
    limit: int = 100,
    offset: int = 0,
    field: str | None = None,
) -> tuple[list[dict], int]:
    """Search credentials by domain, username, password, or URL path (origin).
    Returns (results, total_count).
    If `field` is one of 'domain', 'username', 'password', 'origin', the search
    is restricted to that column only; otherwise all four columns are searched.
    'origin' holds the full URL, so `field=origin` searches URL paths
    (e.g. 'github.com/login')."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return [], 0
    if not query.strip():
        return [], 0
    # Whitelist the column filter — never interpolate raw user input into SQL.
    valid_fields = {"domain", "username", "password", "origin"}
    field = field if field in valid_fields else None
    # A bare query matching a URL shape ("host/path") is ambiguous in the
    # all-fields mode: the domain column holds only the host, so the path half
    # would never match. Search origin only in that case so "github.com/login"
    # behaves intuitively instead of returning 0 rows.
    if field is None and _looks_like_domain_path(query):
        field = "origin"
    if field == "origin":
        # Origins are stored without a trailing slash ("https://github.com"), so a
        # typed "github.com/" would otherwise match nothing.
        stripped = query.strip().rstrip("/")
        if len(stripped) >= 3:
            query = stripped
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        _apply_pragmas(conn)
        # Trigram tokenizer requires >= 3 chars; fall back to LIKE for shorter queries.
        if len(query) >= 3:
            # FTS5 MATCH against the trigram index — uses inverted index, not a scan.
            # Double-quote the query to treat it as a phrase; trigram handles substring.
            # When field is set, prefix with `column :` to restrict the match to one
            # column of the fts5 table.
            fts_query = _fts_escape(query)
            if field:
                fts_query = f"{field} : {fts_query}"
            count_row = conn.execute(
                # Join so the count matches the rows query exactly: a MATCH against the
                # raw index still returns rowids whose credentials row was deleted
                # (tombstoned entries survive until the next segment merge), which
                # over-reports the total and produces empty trailing pages.
                # NOTE: with an aliased FTS table, MATCH must use the table name
                # (`credentials_fts`), not the alias — `f MATCH ?` is "no such column".
                """SELECT COUNT(*) FROM credentials_fts f
                   JOIN credentials c ON c.id = f.rowid
                   WHERE credentials_fts MATCH ?""",
                (fts_query,),
            ).fetchone()
            total = count_row[0] if count_row else 0
            rows = conn.execute(
                """SELECT c.* FROM credentials c
                   JOIN credentials_fts f ON c.id = f.rowid
                   WHERE credentials_fts MATCH ?
                   ORDER BY c.imported_at DESC, c.id DESC
                   LIMIT ? OFFSET ?""",
                (fts_query, limit, offset),
            ).fetchall()
        else:
            # Short query: fall back to prefix LIKE on indexed columns.
            # `origin` is not indexed, so it is only scanned when it is the
            # explicitly selected field (avoids a full scan on every 1-2 char query).
            pattern = f"{query}%"
            if field:
                count_row = conn.execute(
                    f"""SELECT COUNT(*) FROM credentials
                       WHERE {field} LIKE ?""",
                    (pattern,),
                ).fetchone()
                total = count_row[0] if count_row else 0
                rows = conn.execute(
                    f"""SELECT * FROM credentials
                       WHERE {field} LIKE ?
                       ORDER BY imported_at DESC, id DESC
                       LIMIT ? OFFSET ?""",
                    (pattern, limit, offset),
                ).fetchall()
            else:
                count_row = conn.execute(
                    """SELECT COUNT(*) FROM credentials
                       WHERE domain LIKE ? OR username LIKE ? OR password LIKE ?""",
                    (pattern, pattern, pattern),
                ).fetchone()
                total = count_row[0] if count_row else 0
                rows = conn.execute(
                    """SELECT * FROM credentials
                       WHERE domain LIKE ? OR username LIKE ? OR password LIKE ?
                       ORDER BY imported_at DESC, id DESC
                       LIMIT ? OFFSET ?""",
                    (pattern, pattern, pattern, limit, offset),
                ).fetchall()
    return [dict(r) for r in rows], total


# A query shaped like "host.tld/path" — the path half only exists in `origin`, so
# all-fields search routes it to the origin column. A bare domain must NOT be
# routed: 5M+ rows have an empty origin, and they would be lost (the domain
# column still covers them).
_DOMAIN_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}/\S*$", re.I)


def _looks_like_domain_path(query: str) -> bool:
    """True if `query` is a domain followed by a '/' path."""
    text = query.strip()
    if not text or text.startswith("http"):
        return False
    return bool(_DOMAIN_PATH_RE.match(text))


def _fts_escape(query: str) -> str:
    """Escape a raw search string into an FTS5 MATCH expression.

    We wrap the query in double-quotes so FTS5 treats it as a single phrase.
    The trigram tokenizer then does substring matching on the full query.
    Internal double-quotes are doubled per FTS5 string syntax.
    """
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


def stats(db_path: Path | None = None) -> dict:
    """Return summary statistics about the DB."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return {"total": 0, "domains": 0, "messages": 0}
    with sqlite3.connect(str(db_path)) as conn:
        _apply_pragmas(conn)
        total = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
        domains = conn.execute(
            "SELECT COUNT(DISTINCT domain) FROM credentials"
        ).fetchone()[0]
        messages = conn.execute(
            "SELECT COUNT(DISTINCT msg_id) FROM credentials"
        ).fetchone()[0]
    return {"total": total, "domains": domains, "messages": messages}


# ── user auth ───────────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()
    return f"{salt}${pwd_hash}"


def _verify_password(password: str, stored: str) -> bool:
    salt, pwd_hash = stored.split("$", 1)
    return (
        hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
        == pwd_hash
    )


def seed_admin(username: str, password: str, db_path: Path | None = None) -> None:
    db_path = db_path or DB_PATH
    init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username=?", (username,)
        ).fetchone()
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
        existing = conn.execute(
            "SELECT id FROM users WHERE username=?", (username,)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?,?)",
            (username, _hash_password(password)),
        )
        conn.commit()
    return True


def authenticate(
    username: str, password: str, db_path: Path | None = None
) -> dict | None:
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
        # Stamp last_login on every successful authentication. Best-effort: a
        # stamp failure must never block login. Stemmed inside the same `with`
        # so the transaction commits when the context manager exits.
        try:
            conn.execute(
                "UPDATE users SET last_login=datetime('now') WHERE id=?",
                (user["id"],),
            )
            user["last_login"] = conn.execute(
                "SELECT last_login FROM users WHERE id=?", (user["id"],)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
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


# ── search audit log ────────────────────────────────────────────────────


def log_search(
    user_id: int,
    username: str,
    query: str,
    result_count: int,
    db_path: Path | None = None,
    ip_address: str | None = None,
) -> None:
    """Persist one search executed by an approved user. Called by search_server
    after every successful /search and /download. Silent on failure — search
    audit logging must never block or break a search."""
    db_path = db_path or DB_PATH
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """INSERT INTO search_log (user_id, username, query, result_count, ip_address)
                   VALUES (?,?,?,?,?)""",
                (user_id, username, query, result_count, ip_address),
            )
            conn.commit()
    except sqlite3.Error as exc:  # pragma: no cover - best-effort audit trail
        log.warning("failed to log search for user %s: %s", username, exc)


def list_search_logs(
    db_path: Path | None = None,
    limit: int = 200,
    offset: int = 0,
    user_filter: str | None = None,
    query_filter: str | None = None,
    user_id: int | None = None,
) -> tuple[list[dict], int]:
    """Return (search_log_rows, total_count) for the admin review view.
    newest first. Optional username/query substring filters, plus an exact
    user_id filter used to scope a user's own recent searches.

    LIKE filters are applied via parameterized placeholders. A NULL filter
    matches everything; the SQL string is fully static and no f-string /
    interpolation reaches the query text."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return [], 0
    user_pat = f"%{user_filter}%" if user_filter else "%"
    query_pat = f"%{query_filter}%" if query_filter else "%"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if user_id is not None:
            total = conn.execute(
                "SELECT COUNT(*) FROM search_log "
                "WHERE username LIKE ? AND query LIKE ? AND user_id = ?",
                (user_pat, query_pat, user_id),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM search_log "
                "WHERE username LIKE ? AND query LIKE ? AND user_id = ? "
                "ORDER BY searched_at DESC, id DESC "
                "LIMIT ? OFFSET ?",
                (user_pat, query_pat, user_id, limit, offset),
            ).fetchall()
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM search_log WHERE username LIKE ? AND query LIKE ?",
                (user_pat, query_pat),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM search_log "
                "WHERE username LIKE ? AND query LIKE ? "
                "ORDER BY searched_at DESC, id DESC "
                "LIMIT ? OFFSET ?",
                (user_pat, query_pat, limit, offset),
            ).fetchall()
    return [dict(r) for r in rows], total


def search_stats_by_user(db_path: Path | None = None, limit: int = 50) -> list[dict]:
    """Per-user search activity summary for the admin dashboard cards."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT username,
                      COUNT(*)            AS searches,
                      MAX(searched_at)    AS last_search,
                      SUM(result_count)   AS results_viewed
               FROM search_log
               GROUP BY username
               ORDER BY searches DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_users(db_path: Path | None = None) -> list[dict]:
    """All registered users with registration time + last login time.

    For the admin user-list dashboard. newest registration first.
    last_login is NULL if the user has never signed in. Safe on DBs that
    pre-date the last_login column: authenticate() will have triggered the
    ALTER TABLE migration on first login, but if no login has happened yet
    since the deploy, we guard with COALESCE so missing column still returns."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if "last_login" in cols:
            rows = conn.execute(
                "SELECT id, username, approved, is_admin, created_at, last_login "
                "FROM users ORDER BY created_at DESC, id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, username, approved, is_admin, created_at, NULL AS last_login "
                "FROM users ORDER BY created_at DESC, id DESC"
            ).fetchall()
    return [dict(r) for r in rows]
