"""Tests for credential extraction + domain hits (plain text outputs)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
from domain_hits import (
    collect_credentials,
    count_domain_hits,
    write_credentials,
    write_domain_hits,
)

sample = [
    {"artifact_type": "credential", "duplicate_count": 2,
     "payload": {"origin": "https://facebook.com/login", "username": "a@b.c",
                 "password": "hunter2"}},
    {"artifact_type": "credential", "duplicate_count": 9,
     "payload": {"origin": "https://facebook.com/other", "username": "x",
                 "password": "hunter2"}},  # distinct line, both kept
    {"artifact_type": "credential", "duplicate_count": 1,
     "payload": {"origin": "https://WWW.Facebook.com/x", "username": "a@b.c",
                 "password": "hunter2"}},  # exact dup of line 1, dropped
    {"artifact_type": "credential", "duplicate_count": 1,
     "payload": {"origin": "https://github.com", "username": "y",
                 "password": ""}},  # empty password skipped
    {"artifact_type": "credential", "duplicate_count": 1,
     "payload": {"origin": "not a url", "username": "z", "password": "p"}},
    {"artifact_type": "autofill", "duplicate_count": 1,
     "payload": {"origin": "https://auto.example.com", "value": "nope"}},
    {"artifact_type": "cookie", "duplicate_count": 1,
     "payload": {"domain": "c.example.com", "value": "v"}},
]

with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "records.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in sample))

    creds = collect_credentials([f])
    assert "facebook.com:a@b.c:hunter2" in creds, creds
    assert "facebook.com:x:hunter2" in creds, creds
    assert creds.count("facebook.com:a@b.c:hunter2") == 1, creds
    assert creds == sorted(creds), creds
    assert not any(c.startswith("github.com") for c in creds), creds
    assert not any(c.startswith("auto.example") for c in creds), creds
    assert not any(c.startswith("cookie") or c.startswith("c.example") for c in creds), creds
    assert not any("not a url" in c for c in creds), creds

    cf = write_credentials(creds, Path(td) / "credentials.txt")
    text = cf.read_text()
    assert text.count("\n") == len(creds)
    assert "facebook.com:a@b.c:hunter2\n" in text

    counts = count_domain_hits([f])
    assert counts["facebook.com"] == 12, counts  # 2 + 9 + 1
    assert counts["github.com"] == 1, counts
    assert counts["auto.example.com"] == 1, counts
    assert "c.example.com" not in counts, counts
    dhf = write_domain_hits(counts, Path(td) / "domain_hits.txt")
    dhtext = dhf.read_text()
    assert "facebook.com : 12\n" in dhtext, dhtext

    print("CREDENTIALS + DOMAIN HITS TESTS PASSED")
