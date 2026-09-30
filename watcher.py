#!/usr/bin/env python3
"""Watch a Telegram channel with your user account; download and extract archives.

Posts must contain a document (.zip/.7z/.rar) and a password line like:

    .pass: secret123

Login is one-time: run make_session.py to generate TG_SESSION_STRING, put it in
.env (or export it on your VPS), then just run this script forever.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic, time
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

# Load .env BEFORE importing local modules: credentials_db reads
# CREDDB_DB_PATH from the environment at import time, and without this
# ordering every ingest silently goes to downloads/credentials.db instead
# of the main DB.
load_dotenv()

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from extractor import extract_archive, is_supported_archive, password_candidates
from log_parser import parse_extracted_dirs
from domain_hits import (
    collect_credentials,
    count_domain_hits,
    records_files_for,
    write_credentials,
    write_domain_hits,
)
from credentials_db import ingest_records, init_db

API_ID = os.getenv("TG_API_ID")
API_HASH = os.getenv("TG_API_HASH")
SESSION_STRING = os.getenv("TG_SESSION_STRING")
SESSION_FILE = os.getenv("TG_SESSION_FILE", "watcher-session")
CHANNEL = os.getenv("CHANNEL")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))

BACKFILL_HOURS = float(os.getenv("BACKFILL_HOURS", "24"))
SWEEP_HOURS = float(os.getenv("SWEEP_HOURS", "2"))
DOWNLOAD_BOTS = {
    name.strip().lstrip("@").lower()
    for name in os.getenv("DOWNLOAD_BOTS", "boxedrobot").split(",")
    if name.strip()
}
BOT_RESPONSE_TIMEOUT = float(os.getenv("BOT_RESPONSE_TIMEOUT", "120"))
PARSE_LOGS = os.getenv("PARSE_LOGS", "1").strip().lower() in {"1", "true", "yes", "on"}
# Run each parse batch in a subprocess (python -m stealer_log_parser) so its
# memory is freed to the OS between batches. Required on small-RAM VPSes:
# in-process parsing of 30k-file batches OOM-kills a 2GB box.
PARSE_SUBPROCESS = os.getenv("PARSE_SUBPROCESS", "0").strip().lower() in {"1", "true", "yes", "on"}
PARSER_REDACTION = os.getenv("PARSER_REDACTION", "none")
PARSER_FAMILY = os.getenv("PARSER_FAMILY", "auto")
PARSE_MAX_TOTAL_MB = float(os.getenv("PARSE_MAX_TOTAL_MB", "6144"))
PARSE_MAX_FILES = int(os.getenv("PARSE_MAX_FILES", "300000"))
PARSE_BATCH_MB = float(os.getenv("PARSE_BATCH_MB", "512"))
PARSE_BATCH_FILES = int(os.getenv("PARSE_BATCH_FILES", "4000"))
# Cap on the parser subprocess's address space (MB, posix only). A batch that
# needs more fails with a clean MemoryError (manifest-less exit) instead of
# being killed by the kernel OOM killer - which used to take down the whole
# watcher unit mid-run (systemd oom-kill restart loop).
PARSE_SUBPROCESS_RSS_MB = float(os.getenv("PARSE_SUBPROCESS_RSS_MB", "1200"))
# A failed batch is re-split into 4x smaller groups per retry level, down to
# these floors, before its items are recorded as unparseable and quarantined.
PARSE_MIN_SUBBATCH_MB = float(os.getenv("PARSE_MIN_SUBBATCH_MB", "16"))
PARSE_MIN_SUBBATCH_FILES = int(os.getenv("PARSE_MIN_SUBBATCH_FILES", "64"))
# Safety cap on total parse batch attempts (incl. re-splits) per message.
PARSE_MAX_ATTEMPTS = int(os.getenv("PARSE_MAX_ATTEMPTS", "400"))
# Items (dirs/files) whose parsing permanently failed for the last processed
# message; consumed by the pipeline step after parse so they can be moved to
# downloads/_quarantine/msg<id>/ instead of being deleted with the extraction.
_LAST_PARSE_FAILED: dict[int, list[str]] = {}
GDRIVE_REMOTE = os.getenv("GDRIVE_REMOTE", "").strip()
GDRIVE_FOLDER = os.getenv("GDRIVE_FOLDER", "telegram-watcher").strip()
UPLOAD_URL = os.getenv("UPLOAD_URL", "").strip().rstrip("/")
UPLOAD_CHUNK_MB = float(os.getenv("UPLOAD_CHUNK_MB", "50"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
# Telegram FloodWait <= this many seconds is slept off then retried once;
# longer waits leave the message pending (not counted as a failure).
FLOOD_WAIT_SLEEP_MAX = int(os.getenv("FLOOD_WAIT_SLEEP_MAX", "900"))
# Low-disk guard: refuse new downloads unless this many GB stay free
# (on top of the archive + estimated extraction+parse size).
MIN_FREE_GB = float(os.getenv("MIN_FREE_GB", "3"))
# Estimated total disk cost of one archive as a multiple of its compressed
# size (extraction + records.jsonl parse output). Log-heavy archives expand
# to ~10x; 6 is a workable default that still lets big rars through when
# disk has room.
DISK_SIZE_MULTIPLIER = float(os.getenv("DISK_SIZE_MULTIPLIER", "6"))
# When free disk drops below this, failed-extraction archives are deleted
# instead of kept for retry (re-downloaded if a retry ever succeeds later).
KEEP_ARCHIVE_MIN_FREE_GB = float(os.getenv("KEEP_ARCHIVE_MIN_FREE_GB", "10"))
# Pipeline requeue: failed (counted) or pending (disk/floodwait/system)
# messages are automatically retried after this many minutes instead of
# waiting for the next service restart/sweep.
RETRY_DELAY_MIN = float(os.getenv("RETRY_DELAY_MIN", "60"))
MAX_STATE = 20000
FAILURES_FILE = Path(os.getenv("FAILURES_FILE", "failures.json"))
# Pipeline heartbeat: the worker refreshes this file at every step so the
# external health monitor can detect a stuck pipeline.
HEARTBEAT_FILE = Path(os.getenv("HEARTBEAT_FILE", ".pipeline_heartbeat"))


class PipelineError(Exception):
    """A pipeline step failed.

    counted=True  -> data-quality failure (counts toward MAX_RETRIES abandon)
    counted=False -> system/transient failure (stays pending, retried, alert)
    """

    def __init__(self, message: str, counted: bool = False):
        super().__init__(message)
        self.counted = counted


def heartbeat(step: str, msg_id: int = 0, detail: str = "") -> None:
    """Best-effort heartbeat write; never raises."""
    try:
        HEARTBEAT_FILE.write_text(
            json.dumps({"step": step, "msg_id": msg_id, "detail": detail, "ts": time()})
        )
    except OSError:
        pass


log = logging.getLogger("watcher")

_parse_lock = asyncio.Lock()
_work: asyncio.Queue = asyncio.Queue()  # serial pipeline work queue (msg refs)
_failures: dict = {}


def load_failures() -> None:
    global _failures
    try:
        _failures = json.loads(FAILURES_FILE.read_text(encoding="utf-8"))
    except Exception:
        _failures = {}


def save_failures() -> None:
    tmp = FAILURES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_failures), encoding="utf-8")
    tmp.replace(FAILURES_FILE)


def record_failure(msg_id: int, reason: str) -> bool:
    """Record a failure attempt for msg_id. Returns True if the message has
    exceeded MAX_RETRIES and should be abandoned (added to processed)."""
    key = str(msg_id)
    _failures[key] = {
        "count": _failures.get(key, {}).get("count", 0) + 1,
        "last_reason": reason,
    }
    failed = _failures[key]["count"] >= MAX_RETRIES
    save_failures()
    return failed


def clear_failure(msg_id: int) -> None:
    key = str(msg_id)
    if key in _failures:
        del _failures[key]
        save_failures()


def _free_bytes(path: Path) -> int:
    """Free bytes on the filesystem containing `path`."""
    return shutil.disk_usage(str(path)).free


def cleanup_ingested_leftovers(processed: set) -> int:
    """Delete download leftovers (extracted dirs + source archives) for msg
    ids already in `processed`. Protects against the ENOSPC cascade where a
    mid-run cleanup failure leaves tens of GB behind and crashes the watcher
    on save_state. Returns bytes freed."""
    freed = 0
    if not DOWNLOAD_DIR.exists():
        return 0
    proc_ids = {str(x).rsplit(":", 1)[-1] for x in processed}
    for entry in sorted(DOWNLOAD_DIR.iterdir()):
        m = re.match(r"^(\d+)_", entry.name)
        if not m or m.group(1) not in proc_ids:
            continue
        try:
            if entry.is_dir():
                freed += sum(
                    f.stat().st_size for f in entry.rglob("*") if f.is_file()
                )
                shutil.rmtree(entry, ignore_errors=True)
            elif entry.is_file():
                freed += entry.stat().st_size
                entry.unlink()
            log.info("disk guard: removed ingested leftover %s", entry.name)
        except OSError as exc:
            log.warning("disk guard: could not remove %s: %s", entry, exc)
    return freed


def _dir_metrics(root: Path) -> tuple[int, int]:
    """Total bytes and file count under `root` (cheap stat walk)."""
    total = 0
    count = 0
    for p in root.rglob("*"):
        if p.is_file():
            count += 1
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total, count


_BASH_BIN = shutil.which("bash") or ""


def _run_parser_cli(batch: list[Path], out_dir: Path) -> dict | None:
    """Parse one batch via the parser CLI as a subprocess. Memory is freed to
    the OS when the process exits, preventing OOM on small-RAM VPSes. The
    subprocess's address space is capped via ulimit (PARSE_SUBPROCESS_RSS_MB)
    so an oversized batch dies with a clean MemoryError instead of dragging
    the whole watcher unit into the kernel OOM killer. The summary is read
    back from the atomic manifest.json the parser writes last. Returns None
    only if the subprocess failed to start."""
    cmd = [
        sys.executable,
        "-m",
        "stealer_log_parser",
        "parse",
        *[str(p) for p in batch],
        "--output",
        str(out_dir),
        "--redaction",
        PARSER_REDACTION,
        "--family",
        PARSER_FAMILY,
        "--overwrite",
        "--allow-insecure-permissions",
        "--quiet",
    ]
    if os.name == "posix" and PARSE_SUBPROCESS_RSS_MB > 0 and _BASH_BIN:
        cmd = [
            _BASH_BIN,
            "-c",
            f"ulimit -v {int(PARSE_SUBPROCESS_RSS_MB * 1024)}; exec \"$@\"",
            "--",
            *cmd,
        ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=14 * 3600)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("parser subprocess failed to run: %s", e)
        return None
    manifest = out_dir / "manifest.json"
    if not manifest.is_file():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        log.warning("parser subprocess produced no manifest (rc=%s): %s", proc.returncode, tail)
        return None
    try:
        m = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("manifest.json unreadable: %s", e)
        return None
    counts = m.get("diagnostic_counts") or {}
    # Manifest has no exit code and no recoverable/unrecoverable breakdown, so
    # treat status == "failed" (or a zero-record run) as fatal; otherwise the
    # outputs count as usable - archive-safety 'error' diagnostics on cache
    # blobs still leave valid records behind, and garbage is filtered at ingest.
    fatal = m.get("status") == "failed" or m.get("record_count", 0) == 0
    return {
        "record_count": m.get("record_count", 0),
        "accepted_files": m.get("accepted_virtual_files", 0),
        "skipped_files": m.get("skipped_virtual_files", 0),
        "exit_code": 3 if fatal else 0,
        "family": (m.get("family") or {}).get("selected", "unknown"),
        "family_score": (m.get("family") or {}).get("score", 0.0),
    }


def _pack_batches(
    items: list[Path], *, batch_mb: float, batch_files: int
) -> list[list[Path]]:
    """Greedy-pick `items` (dirs or files) into size-bounded batches."""
    batch_bytes = batch_mb * 1024 * 1024
    batches: list[list[Path]] = []
    current: list[Path] = []
    current_bytes = 0
    current_files = 0
    for item in items:
        if item.is_dir():
            total, count = _dir_metrics(item)
        else:
            try:
                total, count = item.stat().st_size, 1
            except OSError:
                continue
        if total > PARSE_MAX_TOTAL_MB * 1024 * 1024 or count > PARSE_MAX_FILES:
            log.warning(
                "parsing: item %s (%.1f MB / %d files) exceeds PARSE_MAX_*; skipping it",
                item.name,
                total / 1e6,
                count,
            )
            continue
        if current and (
            current_bytes + total > batch_bytes
            or current_files + count > batch_files
        ):
            batches.append(current)
            current = []
            current_bytes = 0
            current_files = 0
        current.append(item)
        current_bytes += total
        current_files += count
    if current:
        batches.append(current)
    return batches


def _re_split_items(batch: list[Path], depth: int) -> list[list[Path]]:
    """Split a failed batch into ~4x smaller groups for the next retry."""
    divisor = 4 ** (depth + 1)
    sub_mb = max(PARSE_MIN_SUBBATCH_MB, PARSE_BATCH_MB / divisor)
    sub_files = max(PARSE_MIN_SUBBATCH_FILES, PARSE_BATCH_FILES // divisor)
    if len(batch) == 1:
        # One dir left and it still fails: split at file level.
        p = batch[0]
        if not p.is_dir():
            return []
        files = sorted(x for x in p.rglob("*") if x.is_file())
        if not files:
            return []
        return _pack_batches(files, batch_mb=sub_mb, batch_files=sub_files)
    return _pack_batches(batch, batch_mb=sub_mb, batch_files=sub_files)


def _parse_batches(source_dir: Path) -> list[list[Path]]:
    """Split `source_dir` into size-bounded batches of its children.
    Oversized extractions (stealer dumps grouped in per-machine folders) are
    parsed in batches so peak memory stays near PARSE_BATCH_MB. If the dir
    contains a single nested folder (common for wrapped archives), descend
    into it."""
    root = Path(source_dir)
    for _ in range(4):
        children = sorted(p for p in root.iterdir() if p.is_dir())
        files = sorted(p for p in root.iterdir() if p.is_file())
        if len(children) == 1 and not files:
            root = children[0]
            continue
        break
    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if not dirs:
        return [[root]]
    batches = _pack_batches(
        dirs, batch_mb=PARSE_BATCH_MB, batch_files=PARSE_BATCH_FILES
    )
    if not batches:
        return [[root]]
    return batches


async def _parse_and_verify(msg_id: int, source_dir: Path, output_dir: Path) -> bool:
    """Pipeline step: parse. Blocking (awaited inline), serialized by
    _parse_lock so a small VM never parses two archives at once. Oversized
    extractions are split into batches. Returns True only if at least one
    batch exited 0 (usable output)."""
    total, count = _dir_metrics(source_dir)
    if total > PARSE_MAX_TOTAL_MB * 1024 * 1024 or count > PARSE_MAX_FILES:
        log.info(
            "msg %d: extracting dir is %.1f MB / %d files - parsing in %d batch(es)",
            msg_id,
            total / 1e6,
            count,
            len(_parse_batches(source_dir)),
        )
    else:
        log.info(
            "msg %d: parsing extracted logs (%.1f MB / %d files)...",
            msg_id,
            total / 1e6,
            count,
        )

    batches = _parse_batches(source_dir)
    grand = {"record_count": 0, "accepted_files": 0, "skipped_files": 0}
    ok = False

    def _usable(out_dir: Path) -> bool:
        """A batch counts as parsed when it produced a non-empty records.jsonl
        AND the final atomic manifest.json marker. The parser flags batches
        with any non-recoverable diagnostic (e.g. ARCHIVE_SAFETY on random
        cache files that happen to look like tars) with exit code 3 even when
        it wrote every other record - those outputs are real data, and
        per-record garbage is filtered at ingest anyway."""
        try:
            return (
                (out_dir / "records.jsonl").stat().st_size > 0
                and (out_dir / "manifest.json").is_file()
            )
        except OSError:
            return False

    async with _parse_lock:
        attempts = 0
        # Work queue of (depth, batch, out_name). Failed batches are re-split
        # into 4x smaller groups (deeper levels split single dirs at file
        # level); anything still unparseable is recorded for quarantine.
        level: list[tuple[int, list[Path], str]] = [
            (0, batch, f"part-{i:03d}") for i, batch in enumerate(batches, start=1)
        ]
        idx = 0
        while idx < len(level):
            depth, batch, out_name = level[idx]
            idx += 1
            attempts += 1
            if attempts > PARSE_MAX_ATTEMPTS:
                log.warning(
                    "msg %d: parse attempt budget exhausted - quarantining %d item(s)",
                    msg_id,
                    len(batch),
                )
                _LAST_PARSE_FAILED.setdefault(msg_id, []).extend(
                    str(p) for p in batch
                )
                continue
            batch_out = (
                output_dir / out_name
                if len(batches) > 1 or depth > 0
                else output_dir
            )
            if _usable(batch_out):
                log.info("msg %d: batch %s already parsed - skipping", msg_id, out_name)
                ok = True
                continue
            label = ", ".join(p.name for p in batch[:3])
            if len(batch) > 3:
                label += f", ... ({len(batch)} items)"
            log.info(
                "msg %d: parsing batch %s (%s)%s...",
                msg_id,
                out_name,
                label,
                " [subprocess]" if PARSE_SUBPROCESS else "",
            )
            if PARSE_SUBPROCESS:
                result = await asyncio.to_thread(_run_parser_cli, batch, batch_out)
            else:
                result = await asyncio.to_thread(
                    parse_extracted_dirs,
                    batch,
                    batch_out,
                    redaction=PARSER_REDACTION,
                    family=PARSER_FAMILY,
                )
            if result is not None:
                grand["record_count"] += result.get("record_count", 0)
                grand["accepted_files"] += result.get("accepted_files", 0)
                grand["skipped_files"] += result.get("skipped_files", 0)
            if _usable(batch_out):
                ok = True
                extra = (
                    f"records={result.get('record_count', '?')} "
                    if result is not None
                    else "records resumed "
                )
                if result is not None and result.get("exit_code", 0) != 0:
                    extra += f"(exit {result['exit_code']} - still accepted by artifacts)"
                log.info(
                    "msg %d: batch %s parsed %s-> %s",
                    msg_id,
                    out_name,
                    extra,
                    batch_out.name,
                )
                continue
            if result is None:
                reason = "no manifest (OOM-capped or errored)"
            else:
                reason = f"exit code {result['exit_code']}"
            subs = _re_split_items(batch, depth) if depth < 3 else []
            if subs:
                if len(batch) == 1:
                    reason_note = "retrying as file-level pack(s)"
                else:
                    reason_note = "re-splitting into sub-batches"
                log.warning(
                    "msg %d: batch %s failed (%s) - %s: %d pack(s)",
                    msg_id,
                    out_name,
                    reason,
                    reason_note,
                    len(subs),
                )
                level[idx:idx] = [
                    (depth + 1, sub, f"{out_name}.{j:03d}")
                    for j, sub in enumerate(subs, start=1)
                ]
            else:
                log.error(
                    "msg %d: batch %s unparseable (%s) - quarantining %d item(s)",
                    msg_id,
                    out_name,
                    reason,
                    len(batch),
                )
                _LAST_PARSE_FAILED.setdefault(msg_id, []).extend(
                    str(p) for p in batch
                )
    if ok and len(batches) > 1:
        log.info(
            "msg %d: parsed %d records total across %d batches -> %s",
            msg_id,
            grand["record_count"],
            len(batches),
            output_dir,
        )
    return ok


def aggregate_outputs(msg_id: int, output_dir: Path) -> None:
    """Pipeline step: write per-archive + global domain_hits / credentials
    files. Warn-only (aggregation failure never blocks ingestion)."""
    try:
        parts = sorted(output_dir.glob("part-*"))
        if parts:
            for part in parts:
                recs = list(part.glob("records.jsonl"))
                write_domain_hits(count_domain_hits(recs), part / "domain_hits.txt")
                write_credentials(collect_credentials(recs), part / "credentials.txt")
        else:
            recs = records_files_for(output_dir)
            write_domain_hits(count_domain_hits(recs), output_dir / "domain_hits.txt")
            write_credentials(collect_credentials(recs), output_dir / "credentials.txt")
        log.info(
            "msg %d: domain_hits + credentials written for %s", msg_id, output_dir.name
        )
    except Exception as exc:
        log.warning("msg %d: credentials/domain aggregation failed: %s", msg_id, exc)


def upload_outputs(msg_id: int, output_dir: Path) -> None:
    """Pipeline step (optional): copy parsed outputs to the local PC via the
    cloudflared tunnel (or a configured remote). Warn-only."""
    if UPLOAD_URL:
        _upload_via_http(msg_id, output_dir)
    elif GDRIVE_REMOTE:
        _upload_to_drive(msg_id, output_dir)


def quarantine_failed_items(msg_id: int) -> None:
    """Pipeline step: move items whose parsing permanently failed into
    downloads/_quarantine/msg<id>/ (preserved for offline inspection) instead
    of deleting them together with the extraction in _cleanup_files."""
    failed = _LAST_PARSE_FAILED.pop(msg_id, [])
    if not failed:
        return
    qroot = DOWNLOAD_DIR / "_quarantine" / f"msg{msg_id}"
    qroot.mkdir(parents=True, exist_ok=True)
    moved = 0
    for raw in failed:
        p = Path(raw)
        try:
            if not p.exists():
                continue
            dest = qroot / p.name
            base = dest
            n = 1
            while dest.exists():
                dest = base.with_name(f"{base.name}.{n}")
                n += 1
            shutil.move(str(p), str(dest))
            moved += 1
        except OSError as exc:
            log.warning("msg %d: quarantine move failed for %s: %s", msg_id, p, exc)
    log.warning(
        "msg %d: quarantined %d unparseable item(s) under %s",
        msg_id,
        moved,
        qroot,
    )


def _cleanup_files(msg_id: int, *paths: Path) -> None:
    """Pipeline step: delete files after successful ingestion; verify removal."""
    for d in paths:
        try:
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
            elif d.exists():
                d.unlink()
            if d.exists():
                log.warning("msg %d: cleanup verify failed - %s still exists", msg_id, d)
            else:
                log.info("msg %d: cleaned up %s", msg_id, d)
        except Exception as exc:
            log.warning("msg %d: cleanup of %s failed: %s", msg_id, d, exc)


def _upload_via_http(msg_id: int, output_dir: Path) -> bool:
    """Copy the parsed outputs (domain_hits.txt, summary.csv, records.jsonl, ...)
    to the configured UPLOAD_URL (a cloudflared tunnel to the user's PC) via HTTP
    PUT. Files larger than UPLOAD_CHUNK_MB are uploaded in Content-Range chunks
    so they stay under Cloudflare's ~100 MB request cap. Never raises."""
    import subprocess
    from urllib.parse import quote

    chunk_bytes = int(UPLOAD_CHUNK_MB * 1024 * 1024) if UPLOAD_CHUNK_MB else 0

    files = sorted(
        f
        for f in output_dir.rglob("*")
        if f.is_file() and f.name in ("credentials.txt", "domain_hits.txt")
    )
    if not files:
        log.warning("msg %d: nothing to upload for %s", msg_id, output_dir)
        return

    base = quote(output_dir.name)
    ok = 0
    for f in files:
        rel = f.relative_to(output_dir).as_posix()
        dest = f"{UPLOAD_URL}/{base}/{quote(rel)}"
        size = f.stat().st_size
        try:
            if chunk_bytes and size > chunk_bytes:
                sent = 0
                with f.open("rb") as fh:
                    while True:
                        chunk = fh.read(chunk_bytes)
                        if not chunk:
                            break
                        start = sent
                        end = sent + len(chunk) - 1
                        header = f"bytes {start}-{end}/{size}"
                        proc = subprocess.run(
                            [
                                "curl",
                                "-sS",
                                "-f",
                                "-X",
                                "PUT",
                                "-H",
                                f"Content-Range: {header}",
                                "--data-binary",
                                "@-",
                                dest,
                            ],
                            input=chunk,
                            capture_output=True,
                            timeout=900,
                        )
                        if proc.returncode != 0:
                            err = (
                                (proc.stderr or b"")
                                .decode("utf-8", "replace")
                                .strip()[:200]
                            )
                            log.warning(
                                "msg %d: upload chunk of %s failed (rc=%d): %s",
                                msg_id,
                                rel,
                                proc.returncode,
                                err,
                            )
                            break
                        sent += len(chunk)
                if sent >= size:
                    ok += 1
                continue
            proc = subprocess.run(
                ["curl", "-sS", "-f", "-T", str(f), dest],
                capture_output=True,
                text=True,
                timeout=900,
            )
        except Exception as exc:
            log.warning("msg %d: upload of %s failed to run: %s", msg_id, rel, exc)
            continue
        if proc.returncode == 0:
            ok += 1
        else:
            log.warning(
                "msg %d: upload of %s failed (rc=%d): %s",
                msg_id,
                rel,
                proc.returncode,
                (proc.stderr or "").strip()[:200],
            )
    if ok:
        log.info(
            "msg %d: uploaded %d/%d files to %s", msg_id, ok, len(files), UPLOAD_URL
        )
        return True
    log.warning("msg %d: all %d uploads failed to %s", msg_id, len(files), UPLOAD_URL)
    return False


def _upload_to_drive(msg_id: int, output_dir: Path) -> bool:
    """Copy the parsed outputs (records.jsonl + domain_hits.txt per part) to the
    configured Google Drive folder via rclone. Returns True on success."""
    import shutil
    import subprocess

    rclone = shutil.which("rclone")
    if not rclone:
        log.warning("msg %d: GDRIVE_REMOTE set but rclone not found on PATH", msg_id)
        return False
    remote = GDRIVE_REMOTE
    if not remote.endswith(":"):
        remote += ":"
    dest = f"{remote}{GDRIVE_FOLDER}/{output_dir.name}"
    try:
        proc = subprocess.run(
            [rclone, "copy", str(output_dir), dest, "--create-empty-src-dirs"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:
        log.warning("msg %d: drive upload failed to run: %s", msg_id, exc)
        return False
    if proc.returncode != 0:
        log.warning(
            "msg %d: drive upload failed (rc=%d): %s",
            msg_id,
            proc.returncode,
            (proc.stderr or "").strip()[:500],
        )
        return False
    log.info("msg %d: uploaded parsed outputs to %s", msg_id, dest)
    return True


def is_candidate(msg) -> bool:
    """True if the post carries an archive document or a download-bot deep
    link (i.e. belongs on the pipeline queue)."""
    fname = document_name(msg) if msg.document else None
    return bool(fname or bot_deep_link(msg))


def _scrub_partials() -> None:
    """Startup scrub: remove orphan .parsed dirs left behind by restarts.
    Partial ARCHIVES are not scrubbed here - the pipeline's exact-size check
    (expected vs actual bytes) deletes and re-downloads them automatically
    when the message is processed, so complete archives kept for extraction
    retries survive restarts."""
    if not DOWNLOAD_DIR.exists():
        return
    for entry in sorted(DOWNLOAD_DIR.iterdir()):
        try:
            if entry.is_dir() and entry.name.endswith(".parsed"):
                if not (DOWNLOAD_DIR / entry.name[: -len(".parsed")]).exists():
                    shutil.rmtree(entry, ignore_errors=True)
                    log.info("scrub: removed orphan parsed dir %s", entry.name)
        except OSError as exc:
            log.warning("scrub: could not handle %s: %s", entry, exc)


_requeue_tasks: set = set()


def _requeue_later(msg, processed: set) -> None:
    """Re-enqueue a failed/pending message after RETRY_DELAY_MIN so retries
    happen automatically without waiting for the next restart/sweep."""

    async def _later():
        await asyncio.sleep(RETRY_DELAY_MIN * 60)
        if msg.id not in processed:
            _work.put_nowait(msg)
            log.info(
                "pipeline: requeued msg %d for retry (%d queued)", msg.id, _work.qsize()
            )

    task = asyncio.get_running_loop().create_task(_later())
    _requeue_tasks.add(task)
    task.add_done_callback(_requeue_tasks.discard)


async def _pipeline_worker(client: TelegramClient, processed: set, bot_lock: asyncio.Lock) -> None:
    """Single serial worker: pops one message at a time and runs it through
    the full verified pipeline (download -> extract -> parse -> ingest ->
    delete). No two archives are ever in flight at once. Failed (counted) or
    pending (disk/floodwait/system) messages are requeued automatically."""
    while True:
        msg = await _work.get()
        status = "pending"
        try:
            status = await process_message(client, msg, processed, bot_lock)
        except Exception:
            log.exception(
                "pipeline worker: unhandled error on msg %s - continuing",
                getattr(msg, "id", "?"),
            )
        finally:
            _work.task_done()
        if status in ("pending", "counted"):
            log.info(
                "pipeline: msg %d %s - auto-retry in %.0f min",
                getattr(msg, "id", 0),
                status,
                RETRY_DELAY_MIN,
            )
            _requeue_later(msg, processed)


def load_state() -> set:
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8"))["processed"])
    except Exception:
        return set()


def save_state(processed: set) -> None:
    ids = sorted(str(x) for x in processed)[-MAX_STATE:]
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"processed": ids}), encoding="utf-8")
    tmp.replace(STATE_FILE)


def normalize_channel(ref: str):
    """Accept @name, name, https://t.me/name, https://t.me/c/123456/42, or -100... id."""
    ref = ref.strip()
    if "t.me/" in ref:
        part = ref.split("t.me/", 1)[1]
        if part.startswith("c/"):
            return int("-100" + part[2:].split("/")[0].split("?")[0])
        return part.split("/")[0].split("?")[0]
    if ref.lstrip("-").isdigit():
        return int(ref)
    return ref.lstrip("@")


async def resolve_entity(client: TelegramClient, ref):
    ref = normalize_channel(ref)
    try:
        return await client.get_entity(ref)
    except (ValueError, TypeError):
        pass
    # Private channels without a username: scan the account's own dialogs.
    uname = str(ref).lstrip("@").lower()
    async for dlg in client.iter_dialogs():
        username = getattr(dlg.entity, "username", None)
        if username and username.lower() == uname:
            return dlg.entity
        if dlg.name and uname in dlg.name.lower():
            return dlg.entity
    raise SystemExit(
        f"Could not resolve channel '{ref}'. Run make_session.py to list your channels."
    )


def document_name(msg):
    fname = msg.file.name if msg.file else None
    if not fname:
        return None
    fname = Path(fname).name
    return fname if is_supported_archive(fname) else None


def bot_deep_link(msg):
    for row in getattr(msg, "buttons", None) or []:
        for button in row:
            url = getattr(button, "url", None)
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.hostname not in {
                "t.me",
                "www.t.me",
                "telegram.me",
                "www.telegram.me",
            }:
                continue
            bot = parsed.path.strip("/").split("/", 1)[0].lstrip("@").lower()
            payload = parse_qs(parsed.query).get("start", [None])[0]
            if bot in DOWNLOAD_BOTS and payload:
                return bot, payload
    return None


async def request_bot_document(
    client: TelegramClient, bot: str, payload: str, lock: asyncio.Lock
):
    log.info("requesting file from @%s", bot)
    async with lock:
        deadline = monotonic() + BOT_RESPONSE_TIMEOUT
        async with client.conversation(
            bot, timeout=BOT_RESPONSE_TIMEOUT, exclusive=True
        ) as conv:
            await conv.send_message(f"/start {payload}")
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                response = await asyncio.wait_for(
                    conv.get_response(), timeout=remaining
                )
                if response.document:
                    return response


async def _process_message_impl(
    client: TelegramClient, msg, processed: set, bot_lock: asyncio.Lock
) -> None:
    if msg.id in processed:
        return

    source = msg
    fname = document_name(source) if source.document else None
    deep_link = None if fname else bot_deep_link(msg)
    if not fname and not deep_link:
        return "skip"

    candidates = password_candidates(getattr(msg, "message", "") or "")
    if not candidates:
        log.warning("msg %d: no '.pass:' line found in post - leaving pending", msg.id)
        return "skip"

    if deep_link:
        bot, payload = deep_link
        source = None
        for attempt in (1, 2):
            try:
                source = await request_bot_document(client, bot, payload, bot_lock)
                break
            except FloodWaitError as e:
                wait = int(getattr(e, "seconds", 0) or 0)
                if attempt == 1 and 0 < wait <= FLOOD_WAIT_SLEEP_MAX:
                    log.warning(
                        "msg %d: Telegram flood-wait %ds talking to @%s - "
                        "sleeping it off, then one retry",
                        msg.id,
                        wait,
                        bot,
                    )
                    await asyncio.sleep(wait)
                    continue
                log.warning(
                    "msg %d: Telegram flood-wait %ds talking to @%s - leaving "
                    "pending for the next sweep (not counted as a failure)",
                    msg.id,
                    wait,
                    bot,
                )
                return "pending"
            except asyncio.TimeoutError:
                log.error(
                    "msg %d: timed out waiting for @%s to return a file (attempt %d/%d)",
                    msg.id,
                    bot,
                    _failures.get(str(msg.id), {}).get("count", 0) + 1,
                    MAX_RETRIES,
                )
                if record_failure(msg.id, f"bot_timeout:{bot}"):
                    log.warning(
                        "msg %d: abandoning after %d failed bot attempts - marked processed",
                        msg.id,
                        MAX_RETRIES,
                    )
                    processed.add(msg.id)
                    save_state(processed)
                    return "abandoned"
                return "counted"
            except Exception as e:
                log.error("msg %d: @%s file request failed: %s", msg.id, bot, e)
                if record_failure(msg.id, f"bot_error:{e}"):
                    log.warning(
                        "msg %d: abandoning after %d failures - marked processed",
                        msg.id,
                        MAX_RETRIES,
                    )
                    processed.add(msg.id)
                    save_state(processed)
                    return "abandoned"
                return "counted"
        if source is None:
            return "pending"
        fname = document_name(source)
        if not fname:
            log.error(
                "msg %d: @%s returned an unsupported or unnamed document", msg.id, bot
            )
            return "skip"

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / f"{msg.id}_{fname}"
    stem = Path(fname).stem
    dest = DOWNLOAD_DIR / f"{msg.id}_{stem}"
    parse_out = DOWNLOAD_DIR / f"{msg.id}_{stem}.parsed"

    if not PARSE_LOGS:
        # Parsing disabled (historic behavior): mark processed, no parse/ingest.
        processed.add(msg.id)
        save_state(processed)
        clear_failure(msg.id)
        _cleanup_files(msg.id, target, dest, parse_out)
        return "ok"

    try:
        # ---- step: extract-or-download --------------------------------
        total_bytes, _nfiles = _dir_metrics(dest) if dest.exists() else (0, 0)
        if dest.exists() and any(dest.rglob("*")) and total_bytes > 0:
            log.info(
                "msg %d: extracted dir already exists at %s (%.1f MB) - resuming after extraction",
                msg.id,
                dest,
                total_bytes / 1e6,
            )
        else:
            # A dest dir of empty files is extraction garbage (7z leaves empty
            # placeholders when the password is wrong) - remove it.
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
                log.info("msg %d: removed empty/garbage extraction dir %s", msg.id, dest)
            expected = source.document.size or 0
            if target.exists() and expected and target.stat().st_size == expected:
                log.info(
                    "msg %d: archive already on disk (size verified) - retrying extraction",
                    msg.id,
                )
            else:
                if target.exists():
                    # no usable dest dir -> whatever archive file is on disk is
                    # partial (restart mid-download); re-download from scratch.
                    target.unlink()
                needed = (source.document.size or 0) * DISK_SIZE_MULTIPLIER + MIN_FREE_GB * 1024**3
                if _free_bytes(DOWNLOAD_DIR) < needed:
                    freed = cleanup_ingested_leftovers(processed)
                    if freed:
                        log.info(
                            "msg %d: disk guard freed %.1f GB of already-ingested leftovers",
                            msg.id,
                            freed / 1e9,
                        )
                    if _free_bytes(DOWNLOAD_DIR) < needed:
                        log.error(
                            "msg %d: low disk (%.1f GB free, need ~%.1f GB) - leaving pending",
                            msg.id,
                            _free_bytes(DOWNLOAD_DIR) / 1e9,
                            needed / 1e9,
                        )
                        return "pending"
                size_mb = (source.document.size or 0) / 1e6
                log.info("msg %d: downloading %s (%.1f MB)", msg.id, fname, size_mb)
                last = {"pct": -1}

                def progress(cur, total):
                    pct = int(cur * 100 / total) if total else 0
                    if pct >= last["pct"] + 5:
                        last["pct"] = pct
                        heartbeat("download", msg.id, f"{pct}%")
                        log.info(
                            "msg %d: %d%% (%.1f/%.1f MB)", msg.id, pct, cur / 1e6, total / 1e6
                        )

                downloaded = await client.download_media(
                    source, file=str(target), progress_callback=progress
                )
                # ---- verify: exact size ----------------------------------
                if not downloaded or not target.exists() or target.stat().st_size == 0:
                    target.unlink(missing_ok=True)
                    raise PipelineError("Telegram did not return file data")
                if expected and target.stat().st_size != expected:
                    actual = target.stat().st_size
                    target.unlink(missing_ok=True)
                    raise PipelineError(
                        f"download incomplete: {actual}/{expected} bytes"
                    )

            # ---- step: extract -----------------------------------------
            heartbeat("extract", msg.id, fname)
            try:
                extract_archive(target, candidates, dest)
            except Exception as e:
                # 7z leaves empty placeholder files when the password is
                # wrong - scrub them so a later resume can't mistake the
                # garbage for a valid extraction. The archive itself is kept
                # for a cheap extraction retry, unless disk is tight.
                shutil.rmtree(dest, ignore_errors=True)
                if _free_bytes(DOWNLOAD_DIR) < KEEP_ARCHIVE_MIN_FREE_GB * 1024**3:
                    target.unlink(missing_ok=True)
                    log.info(
                        "msg %d: low disk - dropped failed archive %s (will "
                        "re-download if a retry is attempted)",
                        msg.id,
                        target.name,
                    )
                raise PipelineError(f"extraction:{e}", counted=True) from e
            # ---- verify: non-empty extraction -------------------------
            got_bytes, _f = _dir_metrics(dest)
            if got_bytes == 0:
                shutil.rmtree(dest, ignore_errors=True)
                raise PipelineError("extraction produced no data", counted=True)
            log.info(
                "msg %d: extracted %s (pw ok, %.1f MB) -> %s",
                msg.id,
                fname,
                got_bytes / 1e6,
                dest,
            )
            target.unlink(missing_ok=True)

        # ---- step: parse (verified) --------------------------------
        heartbeat("parse", msg.id)
        if not await _parse_and_verify(msg.id, dest, parse_out):
            raise PipelineError("log parsing produced no usable output", counted=True)

        # ---- step: aggregate + upload + ingest ---------------------
        heartbeat("ingest", msg.id)
        quarantine_failed_items(msg.id)
        aggregate_outputs(msg.id, parse_out)
        upload_outputs(msg.id, parse_out)
        try:
            n = ingest_records(msg.id, parse_out)
        except Exception as exc:
            raise PipelineError(f"DB ingestion failed: {exc}") from exc
        log.info("msg %d: ingested %d credentials into SQLite DB", msg.id, n)

        # ---- step: finalize (only after verified ingestion) ---------
        heartbeat("finalize", msg.id)
        processed.add(msg.id)
        save_state(processed)
        clear_failure(msg.id)
        _cleanup_files(msg.id, dest, parse_out)
        log.info(
            "msg %d: pipeline complete (%d more queued)", msg.id, _work.qsize()
        )
        heartbeat("idle", 0)
        return "ok"
    except PipelineError as e:
        if e.counted:
            log.error(
                "msg %d: %s (attempt %d/%d)",
                msg.id,
                e,
                _failures.get(str(msg.id), {}).get("count", 0) + 1,
                MAX_RETRIES,
            )
            if record_failure(msg.id, str(e)):
                log.warning(
                    "msg %d: abandoning after %d failures - marked processed",
                    msg.id,
                    MAX_RETRIES,
                )
                processed.add(msg.id)
                save_state(processed)
                _cleanup_files(msg.id, target, dest, parse_out)
                heartbeat("idle", 0)
                return "abandoned"
            heartbeat("idle", 0)
            return "counted"
        log.error("msg %d: %s - leaving pending for retry (not counted)", msg.id, e)
        heartbeat("idle", 0)
        return "pending"


async def process_message(
    client: TelegramClient, msg, processed: set, bot_lock: asyncio.Lock
) -> None:
    """Queue-facing wrapper: guarantees the heartbeat returns to idle when
    this message leaves the pipeline (success, failure, or early pending)."""
    if msg.id in processed:
        return "skip"
    try:
        return await _process_message_impl(client, msg, processed, bot_lock)
    finally:
        heartbeat("idle", 0)


async def catch_up(
    client: TelegramClient,
    entity,
    hours: float,
    processed: set,
    bot_lock: asyncio.Lock,
    label: str,
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    log.info(
        "%s: processing posts newer than %s UTC",
        label,
        cutoff.strftime("%Y-%m-%d %H:%M"),
    )
    async for msg in client.iter_messages(entity, offset_date=cutoff, reverse=True):
        if msg.id in processed:
            continue
        if is_candidate(msg):
            _work.put_nowait(msg)
    log.info("%s: done - %d msg(s) now queued for the pipeline", label, _work.qsize())


async def run(args) -> None:
    if not (API_ID and API_HASH):
        sys.exit(
            "Set TG_API_ID / TG_API_HASH in .env (get them from https://my.telegram.org). See README."
        )
    if not CHANNEL:
        sys.exit(
            "Set CHANNEL in .env (e.g. @channelname, https://t.me/channelname, or numeric id)."
        )
    session = StringSession(SESSION_STRING) if SESSION_STRING else SESSION_FILE
    client = TelegramClient(session, int(API_ID), API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("Session not authorized - run: python make_session.py  (see README)")
    entity = await resolve_entity(client, CHANNEL)
    title = getattr(entity, "title", None) or CHANNEL

    processed = load_state()
    load_failures()
    bot_lock = asyncio.Lock()
    fresh = not STATE_FILE.exists()
    _scrub_partials()
    freed = cleanup_ingested_leftovers(processed)
    if freed:
        log.info(
            "startup: cleaned %.1f GB of already-ingested download leftovers",
            freed / 1e9,
        )
    log.info("watching channel: %s | downloads -> %s", title, DOWNLOAD_DIR.resolve())

    # Serial pipeline worker: exactly one archive in flight at any time.
    worker_task = asyncio.get_running_loop().create_task(
        _pipeline_worker(client, processed, bot_lock)
    )
    log.info("pipeline: serial worker started (download -> extract -> parse -> ingest -> delete)")

    backfill = (
        args.backfill_hours
        if args.backfill_hours is not None
        else (BACKFILL_HOURS if fresh else 0)
    )
    if backfill > 0:
        await catch_up(client, entity, backfill, processed, bot_lock, "backfill")

    sweep = args.sweep_hours if args.sweep_hours is not None else SWEEP_HOURS
    if sweep > 0:
        await catch_up(client, entity, sweep, processed, bot_lock, "sweep")

    @client.on(events.NewMessage(chats=entity))
    @client.on(events.MessageEdited(chats=entity))
    async def on_message(event):
        # Live posts only enqueue; the serial worker picks them up in order -
        # no concurrent processing with the sweep or each other.
        if is_candidate(event.message) and event.message.id not in processed:
            _work.put_nowait(event.message)
            log.info("live: queued msg %d (%d queued)", event.message.id, _work.qsize())

    log.info("live: waiting for new or edited posts (Ctrl+C to stop)")
    try:
        await client.run_until_disconnected()
    finally:
        worker_task.cancel()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--backfill-hours",
        type=float,
        default=None,
        help="once: process posts from the last N hours (0=off; default: 24 on first run only)",
    )
    ap.add_argument(
        "--sweep-hours",
        type=float,
        default=None,
        help="catch-up window checked on every start (default: 2)",
    )
    args = ap.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
