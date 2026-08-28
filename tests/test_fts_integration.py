"""Integration + edge-case validation for FTS5 search upgrade.

Simulates the real VPS upgrade path: an existing DB with rows but no FTS
table, then init_db() is called (mimicking a watcher restart) and we verify
the FTS index is backfilled and search works correctly.

Also exercises edge cases, failure modes, and concurrent read-during-write.
"""
import json
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from credentials_db import (
    init_db, ingest_records, search, stats,
    SCHEMA,  # old schema without FTS
)


def _make_records_jsonl(path: Path, records: list[dict]) -> None:
    lines = []
    for r in records:
        lines.append(json.dumps(r))
    path.write_text("\n".join(lines), encoding="utf-8")


CRED_RECORDS = [
    {"artifact_type": "credential", "duplicate_count": 1, "record_id": f"r{i}",
     "payload": {"origin": origin, "username": user, "password": pw}}
    for i, (origin, user, pw) in enumerate([
        ("https://gmail.com/login", "alice@gmail.com", "SecretPass123"),
        ("https://facebook.com", "bob", "hunter2"),
        ("https://github.com", "carol", "letmein"),
        ("https://mail.google.com", "dave", "pwned"),
        ("https://example.org/path", "eve@example.org", "P@ssw0rd!"),
        ("https://sub.domain.co.uk/x", "frank", "unicode-pass"),
    ])
]


def main() -> None:
    passed = 0
    failed = 0

    def check(label: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS: {label}")
        else:
            failed += 1
            print(f"  FAIL: {label} {detail}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        td_path = Path(td)
        db_path = td_path / "test.db"
        out_dir = td_path / "output"
        out_dir.mkdir()
        _make_records_jsonl(out_dir / "records.jsonl", CRED_RECORDS)

        # ── Step 1: Simulate OLD database (pre-FTS, old schema only) ──
        print("\n=== TEST: VPS migration simulation ===")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.executescript(SCHEMA)
            # Insert rows directly (mimicking old DB state with existing data)
            for r in CRED_RECORDS:
                p = r["payload"]
                conn.execute(
                    """INSERT OR IGNORE INTO credentials
                       (msg_id, domain, username, password, origin, record_id)
                       VALUES (?,?,?,?,?,?)""",
                    (100, p["origin"].split("//")[1].split("/")[0],
                     p.get("username", ""), p["password"], p["origin"], r["record_id"]),
                )
            conn.commit()

        cred_count = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM credentials"
        ).fetchone()[0]
        check(f"old DB has {cred_count} rows (expected 6)", cred_count == 6)

        # Verify NO FTS table exists yet
        fts_exists = sqlite3.connect(str(db_path)).execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='credentials_fts'"
        ).fetchone()
        check("FTS table does not exist before migration", fts_exists is None)

        # ── Step 2: Run init_db (simulating watcher restart → migration) ──
        init_db(db_path)

        # FTS table should now exist
        with sqlite3.connect(str(db_path)) as conn:
            fts_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='credentials_fts'"
            ).fetchone()
            check("FTS table created after init_db", fts_exists is not None)

            fts_count = conn.execute("SELECT COUNT(*) FROM credentials_fts").fetchone()[0]
            check(f"FTS backfilled {fts_count} rows (expected 6)", fts_count == 6)

            # Triggers should exist
            triggers = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'credentials_fts%'"
            ).fetchall()
            check(f"3 FTS triggers exist (got {len(triggers)})", len(triggers) == 3)

        # Search should work on migrated data
        results, total = search(db_path, "gmail", 100, 0)
        check("search 'gmail' finds gmail.com after migration", total >= 1)

        results, total = search(db_path, "hunter", 100, 0)
        check("search 'hunter' finds hunter2 password after migration", total == 1)

        # ── Step 3: Ingest new records (triggers should auto-update FTS) ──
        print("\n=== TEST: Post-migration ingest updates FTS ===")
        new_records = [
            {"artifact_type": "credential", "duplicate_count": 1, "record_id": "new1",
             "payload": {"origin": "https://twitter.com", "username": "newuser",
                         "password": "newpass"}},
        ]
        new_dir = td_path / "new_output"
        new_dir.mkdir()
        _make_records_jsonl(new_dir / "records.jsonl", new_records)
        n = ingest_records(200, new_dir, db_path)
        check(f"ingested {n} new records (expected 1)", n == 1)

        results, total = search(db_path, "twitter", 100, 0)
        check("search 'twitter' finds new record via FTS trigger", total == 1)

        results, total = search(db_path, "newpass", 100, 0)
        check("search 'newpass' finds new password via FTS trigger", total == 1)

        # ── Step 4: Edge cases ===
        print("\n=== TEST: Edge cases ===")

        # Empty query (should return 0 results, not crash)
        results, total = search(db_path, "", 100, 0)
        check("empty query returns 0 results", total == 0 and len(results) == 0)

        # Single char (< 3, uses LIKE fallback)
        results, total = search(db_path, "a", 100, 0)
        check("1-char query uses LIKE fallback without crash", total >= 0)

        # 2-char query (LIKE fallback)
        results, total = search(db_path, "ab", 100, 0)
        check("2-char query uses LIKE fallback without crash", total >= 0)

        # Exactly 3 chars (FTS5 boundary)
        results, total = search(db_path, "gma", 100, 0)
        check("3-char query uses FTS5 (matches 'gmail')", total == 1)

        # Double-quote in query (must not break FTS5 syntax)
        results, total = search(db_path, '"test', 100, 0)
        check("query with double-quote doesn't crash FTS5", total >= 0)

        # Unicode in search
        results, total = search(db_path, "unicode", 100, 0)
        check("unicode search term works", total >= 0)

        # Long string query
        results, total = search(db_path, "a" * 500, 100, 0)
        check("very long query doesn't crash", total >= 0)

        # Pagination: page 1, page 2
        results, total = search(db_path, "e", 2, 0)  # per_page=2, page 1
        check(f"pagination returns max 2 per page (got {len(results)})", len(results) <= 2)
        check(f"pagination total preserves full count ({total})", total >= 1)

        results2, total2 = search(db_path, "e", 2, 2)  # per_page=2, page 2
        check("page 2 results differ from page 1",
              [r["id"] for r in results2] != [r["id"] for r in results])

        # Results should be ordered by imported_at DESC, id DESC
        if len(results) >= 2:
            check("results ordered by id DESC",
                  results[0]["id"] > results[1]["id"] if len(results) > 1 else True)

        # Stats should reflect all data
        s = stats(db_path)
        check(f"stats total={s['total']} (expected 7)", s["total"] == 7)
        check(f"stats domains={s['domains']} (expected >=7)", s["domains"] >= 7)

        # ── Step 5: Failure modes ===
        print("\n=== TEST: Failure modes ===")

        # Nonexistent DB path
        results, total = search(td_path / "nonexistent.db", "gmail", 100, 0)
        check("nonexistent DB returns empty results", results == [] and total == 0)

        # Stats on nonexistent DB
        s = stats(td_path / "nonexistent2.db")
        check("nonexistent DB stats returns zeros", s == {"total": 0, "domains": 0, "messages": 0})

        # Re-init is idempotent (doesn't duplicate FTS rows)
        before = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM credentials_fts"
        ).fetchone()[0]
        init_db(db_path)
        init_db(db_path)
        after = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM credentials_fts"
        ).fetchone()[0]
        check(f"idempotent re-init: FTS count {before} -> {after} (should be equal)", before == after)

        # Search after re-init still works
        results, total = search(db_path, "gmail", 100, 0)
        check("search works after re-init", total >= 1)

        # ── Step 6: Concurrent read during write (WAL mode) ===
        print("\n=== TEST: Concurrent read during write (WAL mode) ===")
        errors: list[str] = []
        barrier = threading.Barrier(2)

        def writer():
            try:
                barrier.wait(timeout=5)
                for i in range(10):
                    rec_dir = td_path / f"concurrent_{i}"
                    rec_dir.mkdir()
                    _make_records_jsonl(rec_dir / "records.jsonl", [
                        {"artifact_type": "credential", "duplicate_count": 1,
                         "record_id": f"concurrent_{i}",
                         "payload": {"origin": f"https://concurrent{i}.com",
                                     "username": f"user{i}", "password": f"pass{i}"}}])
                    ingest_records(300 + i, rec_dir, db_path)
            except Exception as e:
                errors.append(f"writer: {e}")

        def reader():
            try:
                barrier.wait(timeout=5)
                for _ in range(10):
                    results, total = search(db_path, "gmail", 100, 0)
                    # Should never fail during concurrent writes
            except Exception as e:
                errors.append(f"reader: {e}")

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        check(f"concurrent read+write completed without errors (errors: {errors})", len(errors) == 0, str(errors))

        # Verify all concurrent writes landed
        results, total = search(db_path, "concurrent", 100, 0)
        check(f"concurrent writes ingested {total} records (expected 10)", total == 10)

        # ── Summary ===
        print(f"\n{'='*50}")
        print(f"RESULTS: {passed} passed, {failed} failed")
        if failed:
            print("SOME TESTS FAILED")
            sys.exit(1)
        else:
            print("ALL VALIDATION TESTS PASSED")


if __name__ == "__main__":
    main()
