#!/usr/bin/env python3
"""Repair flattened multi-trio credential rows in a credentials SQLite DB.

The upstream parser used to concatenate several consecutive
``url<delim>user<delim>pass`` trios into one row's fields (extras glued into
password or scattered across username/password/origin). This tool finds such
rows, re-splits the token stream into URL-anchored trios, inserts one clean
row per trio, and deletes the garbled source row.

Safety:
  * DRY-RUN by default: prints a JSON report, writes nothing.
  * ``--apply`` performs the fix. A backup copy is made first
    (``--backup PATH`` to choose the location, default
    ``<db>.bak-flatten``; an existing backup is reused, not overwritten).
  * Clean trios are inserted with INSERT OR IGNORE: the UNIQUE
    (domain, username, password) index silently drops trios that already
    exist in the DB, so existing clean rows are never duplicated. The count
    of already-present trios is derived from total_changes deltas.
  * The garbled row is deleted only when ALL of its trios parsed to clean
    (origin, username, password) triples. Ragged rows are kept and reported
    for manual review.
  * New rows get a synthetic unique record_id
    (``<domain>:<user>:<pass>:flatfix<src_id>``) so the record_id UNIQUE
    constraint can never collide with rows ingested by the normal pipeline.

Usage:
  python fix_flattened_rows.py [--db PATH] [--apply] [--backup PATH]
                               [--json OUT]

Exit codes: 0 ok, 1 usage error, 2 unexpected error.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

# Same URL heuristic as the parser fix (credentials.py _URLISH_RE).
_URLISH_RE = re.compile(
    r"^(?:(?:https?|ftp)://\S|(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:[:/?#]|$))",
    re.IGNORECASE,
)

FIELDS = (
    "id, msg_id, domain, username, password, origin, browser, "
    "source_file, source_sha256, confidence, duplicate_count, family, "
    "record_id, imported_at, source_dir, part"
)

FETCH_SQL = f"""
SELECT {FIELDS}
FROM credentials
WHERE (username LIKE '%' || char(9) || '%'
       OR password LIKE '%' || char(9) || '%'
       OR origin LIKE '%' || char(9) || '%')
"""

INSERT_SQL = """
INSERT OR IGNORE INTO credentials
  (msg_id, domain, username, password, origin, browser,
   source_file, source_sha256, confidence, duplicate_count,
   family, record_id, source_dir, part)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _looks_like_url(token: str) -> bool:
    t = (token or "").strip()
    if not t or len(t) > 2048:
        return False
    if any(ch.isspace() for ch in t):
        return False
    if "@" in t.split("/")[0]:
        return False
    return bool(_URLISH_RE.match(t))


def _hostname(value: str) -> str | None:
    """Mirror credentials_db._hostname for the domain column."""
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
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if any(ch.isspace() or ch in "/\\:?#@[]" for ch in host):
        return None
    return host or None


def split_trios(tokens: list[str]) -> list[list[str]] | None:
    """Split a token stream into URL-anchored groups.

    Returns None when the stream is not cleanly trios (fewer than two URL
    anchors, or any ragged group) - the caller keeps such rows untouched.
    """
    anchors = [i for i, t in enumerate(tokens) if _looks_like_url(t)]
    if len(anchors) < 2:
        return None
    bounds = anchors + [len(tokens)]
    groups = [tokens[a:b] for a, b in zip(bounds, bounds[1:])]
    if any(len(g) != 3 for g in groups):
        return None
    return groups


def row_tokens(row: sqlite3.Row) -> list[str]:
    """Reassemble the flattened token stream from a row's fields.

    The parser emitted trios in source order across (origin, username,
    password), so concatenating those fields in order restores the original
    token stream.
    """
    tokens: list[str] = []
    for field in (row["origin"], row["username"], row["password"]):
        if not field:
            continue
        tokens.extend(t.strip() for t in field.split("\t") if t.strip())
    return tokens


def _example(row: sqlite3.Row, tokens: list[str]) -> dict:
    return {
        "id": row["id"],
        "msg_id": row["msg_id"],
        "domain": row["domain"],
        "tokens": tokens[:12],
    }


def run(db_path: Path, apply: bool, backup: Path | None, json_out: Path | None) -> int:
    if not db_path.exists():
        print(f"ERROR: db not found: {db_path}", file=sys.stderr)
        return 1
    if apply:
        if backup is None:
            backup = db_path.with_name(f"{db_path.name}.bak-flatten")
        if not backup.exists():
            print(f"backup: copying {db_path} -> {backup} ...")
            shutil.copy2(db_path, backup)
        else:
            print(f"backup: reusing existing {backup}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Phase 1 (read-only): collect candidates. The candidate pool is small
    # (thousands of rows), so holding it in memory is fine and avoids
    # interleaving a SELECT cursor with same-table writes.
    candidates: list[sqlite3.Row] = [r for r in conn.execute(FETCH_SQL)]

    stats = {
        "tab_rows_total": len(candidates),
        "flattened_candidates": 0,
        "rows_split": 0,
        "trios_to_emit": 0,
        "trios_already_present": 0,
        "rows_kept_ragged": 0,
        "errors": 0,
    }
    examples_split: list[dict] = []
    examples_ragged: list[dict] = []

    insert_batches: list[list[tuple]] = []
    delete_ids: list[int] = []

    for row in candidates:
        tokens = row_tokens(row)
        if len(tokens) < 6 or not any(_looks_like_url(t) for t in tokens):
            continue  # tabs present but not multi-trio shaped
        stats["flattened_candidates"] += 1
        groups = split_trios(tokens)
        if groups is None:
            stats["rows_kept_ragged"] += 1
            if len(examples_ragged) < 5:
                examples_ragged.append(_example(row, tokens))
            continue
        trio_rows: list[tuple] = []
        ok = True
        for origin, username, password in groups:
            host = _hostname(origin)
            if not host:
                ok = False
                break
            trio_rows.append(
                (
                    row["msg_id"], host, username, password, origin,
                    row["browser"], row["source_file"], row["source_sha256"],
                    row["confidence"], row["duplicate_count"], row["family"],
                    f"{host}:{username}:{password}:flatfix{row['id']}",
                    row["source_dir"], row["part"],
                )
            )
        if not ok:
            stats["rows_kept_ragged"] += 1
            if len(examples_ragged) < 5:
                examples_ragged.append(_example(row, tokens))
            continue
        stats["rows_split"] += 1
        stats["trios_to_emit"] += len(trio_rows)
        insert_batches.append(trio_rows)
        delete_ids.append(row["id"])
        if len(examples_split) < 5:
            examples_split.append(_example(row, tokens))

    # Phase 2 (write): inserts + deletes per batch, all inside one
    # transaction, only when --apply.
    if apply and insert_batches:
        changes_before = conn.total_changes
        conn.execute("BEGIN")
        try:
            for batch, rid in zip(insert_batches, delete_ids):
                conn.executemany(INSERT_SQL, batch)
                conn.execute("DELETE FROM credentials WHERE id=?", (rid,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        changes_after = conn.total_changes
        stats["trios_already_present"] = stats["trios_to_emit"] - (
            changes_after - changes_before
        )
        # changes include the deletes too; recompute precisely:
        # inserted = trios_to_emit - already_present - deletes? No:
        # total_changes counts inserted rows AND deleted rows. So:
        # delta = inserted + deleted -> inserted = delta - len(delete_ids)
        stats["trios_already_present"] = (
            stats["trios_to_emit"] - ((changes_after - changes_before) - len(delete_ids))
        )

    conn.close()

    report = {
        "db": str(db_path),
        "apply": apply,
        "backup": str(backup) if backup else None,
        "stats": stats,
        "examples_split": examples_split,
        "examples_ragged": examples_ragged,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if json_out:
        json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default="downloads/credentials.db")
    ap.add_argument("--apply", action="store_true", help="actually fix (default: dry run)")
    ap.add_argument("--backup", default=None, help="backup path used with --apply")
    ap.add_argument("--json", default=None, help="also write the report to this file")
    args = ap.parse_args()
    try:
        return run(
            Path(args.db),
            args.apply,
            Path(args.backup) if args.backup else None,
            Path(args.json) if args.json else None,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
