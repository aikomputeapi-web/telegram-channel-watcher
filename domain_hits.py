"""Extract credentials (domain:username:password) from parsed records.jsonl.

Reads the parser's records.jsonl files, keeps only credential records, and
writes them as a sorted, deduplicated plain-text file (credentials.txt) - one
line per `domain:username:password`. No JSON schema, no other artifacts.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("credentials")

DOMAIN_ARTIFACTS = {"credential", "autofill"}


def count_domain_hits(records_files) -> Counter:
    """Hits per domain (from credential/autofill origins), using duplicate_count."""
    counts: Counter = Counter()
    for rec in iter_records(records_files):
        if rec.get("artifact_type") not in DOMAIN_ARTIFACTS:
            continue
        payload = rec.get("payload") or {}
        host = _hostname(payload.get("origin"))
        if host:
            counts[host] += int(rec.get("duplicate_count", 1))
    return counts


def write_domain_hits(counts: Counter, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{host} : {hits}\n" for host, hits in counts.most_common()]
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.replace(out_path)
    log.info("wrote %d domain lines to %s", len(lines), out_path)
    return out_path


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


def iter_records(records_files):
    for path in records_files:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            log.warning("cannot read %s: %s", path, exc)


def collect_credentials(records_files) -> list[str]:
    """Unique sorted `domain:username:password` lines from credential records."""
    seen: set[str] = set()
    for rec in iter_records(records_files):
        if rec.get("artifact_type") != "credential":
            continue
        payload = rec.get("payload") or {}
        password = (payload.get("password") or "").strip()
        username = (payload.get("username") or "").strip()
        if not password:
            continue
        if "\n" in username or "\r" in username or "\n" in password or "\r" in password:
            continue
        host = _hostname(payload.get("origin"))
        if not host:
            continue
        seen.add(f"{host}:{username}:{password}")
    return sorted(seen)


def write_credentials(lines: list[str], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    data = "".join(f"{line}\n" for line in lines).encode("utf-8")
    tmp.write_bytes(data)
    tmp.replace(out_path)
    log.info("wrote %d credential lines (%d bytes) to %s", len(lines), len(data), out_path)
    return out_path


def records_files_for(parsed_root: Path) -> list[Path]:
    return sorted(Path(parsed_root).rglob("records.jsonl"))
