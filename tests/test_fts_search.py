"""Tests for FTS5 trigram search in credentials_db."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from credentials_db import init_db, ingest_records, search, stats


# Minimal JSONL that exercises the search paths.
JSONL = """\
{"artifact_type":"credential","duplicate_count":1,"record_id":"r1","payload":{"origin":"https://gmail.com/login","username":"alice@gmail.com","password":"SecretPass123"}}
{"artifact_type":"credential","duplicate_count":1,"record_id":"r2","payload":{"origin":"https://facebook.com","username":"bob","password":"hunter2"}}
{"artifact_type":"credential","duplicate_count":1,"record_id":"r3","payload":{"origin":"https://github.com","username":"carol","password":"letmein"}}
{"artifact_type":"credential","duplicate_count":1,"record_id":"r4","payload":{"origin":"https://mail.google.com","username":"dave","password":"pwned"}}
"""


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "test.db"
        out_dir = Path(td) / "output"
        out_dir.mkdir()

        # Write a records.jsonl for the ingest function to read.
        (out_dir / "records.jsonl").write_text(JSONL, encoding="utf-8")

        # Initialize DB (creates schema + FTS + backfill empty = nothing to backfill).
        init_db(db_path)

        # Ingest records.
        n = ingest_records(100, out_dir, db_path)
        assert n == 4, f"expected 4 rows ingested, got {n}"

        # Verify stats.
        s = stats(db_path)
        assert s["total"] == 4, s
        assert s["domains"] >= 4, s

        # ── FTS5 trigram searches ──

        # Substring "gmail" matches only gmail.com (mail.google.com has no "gma" trigram).
        results, total = search(db_path, "gmail", 100, 0)
        assert total == 1, f"'gmail' should match 1 row, got {total}"
        assert results[0]["domain"] == "gmail.com"

        # Substring "alice" via username.
        results, total = search(db_path, "alice", 100, 0)
        assert total == 1, f"'alice' should match 1 row, got {total}"
        assert "alice@gmail.com" == results[0]["username"]

        # Substring "Secret" via password.
        results, total = search(db_path, "Secret", 100, 0)
        assert total == 1, f"'Secret' should match 1 row, got {total}"
        assert "SecretPass123" == results[0]["password"]

        # Substring "pass" should match both SecretPass123 and nothing else.
        results, total = search(db_path, "pass", 100, 0)
        assert total == 1, f"'pass' should match 1 row, got {total}"

        # Trigram doesn't match <3 chars well — verify fallback to LIKE prefix.
        # "gi" matches github.com (starts with "gi"), but not gmail.com (starts with "gm").
        results, total = search(db_path, "gi", 100, 0)
        assert total == 1, f"'gi' prefix fallback should match github only, got {total}"
        assert results[0]["domain"] == "github.com"

        # No match.
        results, total = search(db_path, "nonexistent", 100, 0)
        assert total == 0, f"expected 0 matches for 'nonexistent', got {total}"

        # ── Re-init should be idempotent (FTS backfill won't duplicate) ──
        init_db(db_path)
        results, total = search(db_path, "mail", 100, 0)
        assert total == 2, f"re-init should preserve FTS data, got {total}"

        print("FTS5 SEARCH TESTS PASSED")


if __name__ == "__main__":
    main()
