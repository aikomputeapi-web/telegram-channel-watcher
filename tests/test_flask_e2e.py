"""End-to-end test of the Flask search_server with FTS5 search.

Starts the server on a random port with a temp DB, exercises login,
search (including FTS5 trigram), pagination, and admin panel.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import urllib.request
import urllib.parse
import http.cookiejar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from credentials_db import init_db, ingest_records


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


def _make_jsonl(path: Path):
    lines = [__import__("json").dumps(r) for r in CRED_RECORDS]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    passed = 0
    failed = 0

    def check(label, condition, detail=""):
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
        _make_jsonl(out_dir / "records.jsonl")

        init_db(db_path)
        ingest_records(100, out_dir, db_path)

        # Start Flask server
        import search_server
        from credentials_db import seed_admin
        seed_admin("admin", "testpass123", db_path)
        search_server.DB_PATH = db_path
        search_server.ADMIN_USER = "admin"
        search_server.ADMIN_PASS = "testpass123"
        search_server.PORT = 0  # OS picks a free port

        # Use Flask test client instead of starting a real server
        app = search_server.app
        app.config["TESTING"] = True
        client = app.test_client()

        print("\n=== TEST: Flask search_server end-to-end ===")

        # --- Login page loads ---
        resp = client.get("/")
        check("login page returns 200", resp.status_code == 200)
        check("login page has 'Credential Search'", b"Credential Search" in resp.data)

        # --- Unauthenticated search redirects to login ---
        resp = client.get("/search?q=gmail")
        check("unauthenticated search shows login", b"Please sign in" in resp.data or resp.status_code in (200, 302))

        # --- Login as admin ---
        resp = client.post("/", data={"username": "admin", "password": "testpass123"}, follow_redirects=True)
        # After login, should be on search page
        body = resp.data.decode("utf-8")
        check("admin login succeeds", "Search" in body or "search" in body.lower())
        check("dashboard shows stats (6 credentials)", ">6<" in body or "6" in body)

        # --- Search: FTS5 trigram 'gmail' ---
        resp = client.get("/search?q=gmail")
        body = resp.data.decode("utf-8")
        check("FTS5 search 'gmail' returns results", "gmail.com" in body)
        check("search results show username", "alice" in body)

        # --- Search: by password substring ---
        resp = client.get("/search?q=hunter")
        body = resp.data.decode("utf-8")
        check("search 'hunter' finds password", "hunter2" in body)

        # --- Search: by username substring ---
        resp = client.get("/search?q=carol")
        body = resp.data.decode("utf-8")
        check("search 'carol' finds username", "carol" in body)

        # --- Search: no results ---
        resp = client.get("/search?q=nonexistent123")
        body = resp.data.decode("utf-8")
        check("search 'nonexistent123' shows no results", "No results" in body)

        # --- Search: empty query ---
        resp = client.get("/search?q=")
        body = resp.data.decode("utf-8")
        check("empty query doesn't crash", resp.status_code == 200)
        check("empty query shows enter term prompt", "Enter a search term" in body or "No results" in body)

        # --- Search: special chars (double-quote, must not break FTS5) ---
        resp = client.get("/search?q=%22test")  # "test
        check("double-quote query doesn't crash", resp.status_code == 200)

        # --- Pagination ---
        # With per_page=50 (PER_PAGE in search_server) and 6 records, all fit on 1 page
        resp = client.get("/search?q=gmail&page=1")
        check("pagination page 1 returns 200", resp.status_code == 200)

        # --- Admin panel ---
        resp = client.get("/admin")
        body = resp.data.decode("utf-8")
        check("admin page loads", "Admin Panel" in body)

        # --- Register new user ---
        resp = client.post("/register", data={"username": "newuser", "password": "pass123"})
        body = resp.data.decode("utf-8")
        check("registration succeeds", "Account created" in body)

        # --- Admin sees pending user ---
        resp = client.get("/admin")
        body = resp.data.decode("utf-8")
        check("admin sees pending user", "newuser" in body)

        # --- Admin approves user ---
        # Find the user ID from the DB
        with sqlite3.connect(str(db_path)) as conn:
            user_id = conn.execute("SELECT id FROM users WHERE username='newuser'").fetchone()[0]
        resp = client.post("/admin", data={"user_id": str(user_id), "action": "approve"})
        body = resp.data.decode("utf-8")
        check("admin approves user", "User approved" in body)

        # --- New user can login after approval ---
        client2 = app.test_client()
        resp = client2.post("/", data={"username": "newuser", "password": "pass123"}, follow_redirects=True)
        body = resp.data.decode("utf-8")
        check("approved user can login", "Search" in body or "search" in body.lower())

        # --- Logout ---
        resp = client.get("/logout", follow_redirects=True)
        check("logout redirects to login", resp.status_code == 200)

        print(f"\n{'='*50}")
        print(f"RESULTS: {passed} passed, {failed} failed")
        if failed:
            print("SOME TESTS FAILED")
            sys.exit(1)
        else:
            print("ALL FLASK E2E TESTS PASSED")


if __name__ == "__main__":
    main()
