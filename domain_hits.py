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

# Max sane password length. Real passwords are short; anything larger is a
# vault-dump blob / LevelDB fragment / JSON config wreck that the upstream
# parser mis-captured. Norton/Bitwarden/NordPass vault dumps routinely hit
# tens of KB.
MAX_PASSWORD_LEN = 4096

# Signatures that mark a "password" as actually a password-manager vault dump
# or app config blob (Norton wax rules, Bitwarden ciphers, NordPass LevelDB,
# Mixpanel telemetry, etc.). Matched case-sensitively against the password.
_GARBAGE_SIGNATURES = (
    # Norton Password Manager wax/login-form rules
    "ParseCriterias",
    "VerifyAttributeByAttachedText",
    "wax_rules_download_timestamp",
    "ParseCriteriasEx",
    "ParseRetryInfo",
    "LoginCategorizations",
    "DomainRules:",
    "UrlRules:",
    "rules_json",
    "global_json",
    "domains_data",
    "Norton Identity Safe",
    # Mixpanel telemetry stored by Norton extension
    "__mp_opt_in_out",
    "__mplss_",
    "_mixpanel",
    # NordPass LevelDB dumps
    'identifier:"nordaccount"',
    "browser_importer",
    "last-heartbeat",
    # Bitwarden vault dumps
    "_ciphers_ciphers",
    "_domainSettings",
    "fillAssistTargetingRulesByServer",
    "equivalentDomains",
    "organizationUseTotp",
)


def count_domain_hits(records_files) -> Counter:
    """Hits per domain (from credential/autofill origins), using duplicate_count."""
    counts: Counter = Counter()
    for rec in iter_records(records_files):
        if rec.get("artifact_type") not in DOMAIN_ARTIFACTS:
            continue
        payload = rec.get("payload") or {}
        host = _hostname(payload.get("origin"))
        if host:
            try:
                counts[host] += int(rec.get("duplicate_count", 1))
            except (TypeError, ValueError):
                counts[host] += 1
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
    host = host.lower()
    while host.startswith("."):
        host = host[1:]
    if host.startswith("www."):
        host = host[4:]
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


def _is_garbage_password(password: str) -> bool:
    """True if `password` is a vault-dump / app-config blob, not a real cred."""
    if len(password) > MAX_PASSWORD_LEN:
        return True
    return any(sig in password for sig in _GARBAGE_SIGNATURES)


def collect_credentials(records_files) -> list[str]:
    """Unique sorted `domain:username:password` lines from credential records."""
    seen: set[str] = set()
    skipped_garbage = 0
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
        # Reject vault-dump blobs (Norton wax rules, Bitwarden ciphers,
        # NordPass LevelDB fragments, Mixpanel telemetry, etc.) that the
        # upstream parser sometimes mis-classifies as credentials. These are
        # multi-KB JSON / binary payloads, not real passwords.
        if _is_garbage_password(password):
            skipped_garbage += 1
            continue
        host = _hostname(payload.get("origin"))
        if not host:
            continue
        seen.add(f"{host}:{username}:{password}")
    if skipped_garbage:
        log.warning("skipped %d garbage vault-dump passwords", skipped_garbage)
    return sorted(seen)


def write_credentials(lines: list[str], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    data = "".join(f"{line}\n" for line in lines).encode("utf-8")
    tmp.write_bytes(data)
    tmp.replace(out_path)
    log.info(
        "wrote %d credential lines (%d bytes) to %s", len(lines), len(data), out_path
    )
    return out_path


def records_files_for(parsed_root: Path) -> list[Path]:
    return sorted(Path(parsed_root).rglob("records.jsonl"))
