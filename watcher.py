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
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from telethon import TelegramClient, events
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

load_dotenv()

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
PARSER_REDACTION = os.getenv("PARSER_REDACTION", "none")
PARSER_FAMILY = os.getenv("PARSER_FAMILY", "auto")
PARSE_MAX_TOTAL_MB = float(os.getenv("PARSE_MAX_TOTAL_MB", "6144"))
PARSE_MAX_FILES = int(os.getenv("PARSE_MAX_FILES", "300000"))
PARSE_BATCH_MB = float(os.getenv("PARSE_BATCH_MB", "512"))
PARSE_BATCH_FILES = int(os.getenv("PARSE_BATCH_FILES", "4000"))
GDRIVE_REMOTE = os.getenv("GDRIVE_REMOTE", "").strip()
GDRIVE_FOLDER = os.getenv("GDRIVE_FOLDER", "telegram-watcher").strip()
UPLOAD_URL = os.getenv("UPLOAD_URL", "").strip().rstrip("/")
UPLOAD_CHUNK_MB = float(os.getenv("UPLOAD_CHUNK_MB", "50"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MAX_STATE = 20000
FAILURES_FILE = Path(os.getenv("FAILURES_FILE", "failures.json"))

log = logging.getLogger("watcher")

_parse_lock = asyncio.Lock()
_parse_tasks: set = set()
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
    _failures[key] = {"count": _failures.get(key, {}).get("count", 0) + 1, "last_reason": reason}
    failed = _failures[key]["count"] >= MAX_RETRIES
    save_failures()
    return failed


def clear_failure(msg_id: int) -> None:
    key = str(msg_id)
    if key in _failures:
        del _failures[key]
        save_failures()


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

    batch_bytes = PARSE_BATCH_MB * 1024 * 1024
    batches: list[list[Path]] = []
    current: list[Path] = []
    current_bytes = 0
    current_files = 0
    for d in dirs:
        total, count = _dir_metrics(d)
        if total > PARSE_MAX_TOTAL_MB * 1024 * 1024 or count > PARSE_MAX_FILES:
            log.warning("parsing %s: subdir %.1f MB / %d files exceeds PARSE_MAX_*; skipping it",
                        d.name, total / 1e6, count)
            continue
        if current and (
            current_bytes + total > batch_bytes
            or current_files + count > PARSE_BATCH_FILES
        ):
            batches.append(current)
            current = []
            current_bytes = 0
            current_files = 0
        current.append(d)
        current_bytes += total
        current_files += count
    if current:
        batches.append(current)
    if not batches:
        return [[root]]
    return batches


async def _parse_in_background(msg_id: int, source_dir: Path, output_dir: Path) -> None:
    """Run the log parser off the event loop; serialize parses so a small VM
    never runs several at once. Oversized extractions are split into batches."""
    total, count = _dir_metrics(source_dir)
    if total > PARSE_MAX_TOTAL_MB * 1024 * 1024 or count > PARSE_MAX_FILES:
        log.info("msg %d: extracting dir is %.1f MB / %d files - parsing in %d batch(es)",
                 msg_id, total / 1e6, count,
                 len(_parse_batches(source_dir)))
    else:
        log.info("msg %d: parsing extracted logs in background (%.1f MB / %d files)...",
                 msg_id, total / 1e6, count)

    batches = _parse_batches(source_dir)
    grand = {"record_count": 0, "accepted_files": 0, "skipped_files": 0}
    ok = False
    async with _parse_lock:
        for i, batch in enumerate(batches, start=1):
            if len(batches) > 1:
                batch_out = output_dir / f"part-{i:03d}"
            else:
                batch_out = output_dir
            label = ", ".join(p.name for p in batch[:3])
            if len(batch) > 3:
                label += f", ... ({len(batch)} items)"
            log.info("msg %d: parsing batch %d/%d (%s)...", msg_id, i, len(batches), label)
            result = await asyncio.to_thread(
                parse_extracted_dirs, batch, batch_out,
                redaction=PARSER_REDACTION, family=PARSER_FAMILY,
            )
            if result is None:
                log.warning("msg %d: batch %d/%d parsing failed (parser not installed or errored)",
                            msg_id, i, len(batches))
                continue
            grand["record_count"] += result.get("record_count", 0)
            grand["accepted_files"] += result.get("accepted_files", 0)
            grand["skipped_files"] += result.get("skipped_files", 0)
            if result["exit_code"] == 0:
                ok = True
                log.info("msg %d: batch %d/%d parsed %d records (family=%s, score=%.3f)",
                         msg_id, i, len(batches), result["record_count"],
                         result["family"], result["family_score"])
            else:
                log.warning("msg %d: batch %d/%d exit code %d (see diagnostics.jsonl)",
                            msg_id, i, len(batches), result["exit_code"])
    if not ok:
        log.warning("msg %d: log parsing produced no usable output; extracted files kept at %s",
                    msg_id, source_dir)
    elif len(batches) > 1:
        log.info("msg %d: parsed %d records total across %d batches -> %s",
                 msg_id, grand["record_count"], len(batches), output_dir)

    if ok:
        _finalize_parsed(msg_id, source_dir, output_dir)
    return


def _finalize_parsed(msg_id: int, source_dir: Path, output_dir: Path) -> None:
    """After an archive finishes parsing: write per-archive + global domain_hits
    files and optionally upload the outputs to the local PC via the cloudflared
    tunnel (or a configured remote). On success the extracted source dir and the
    parsed dir (records.jsonl etc.) are deleted to free disk."""
    try:
        # Per-part (chunk) outputs: domain_hits.txt + credentials.txt
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
        log.info("msg %d: domain_hits + credentials written for %s", msg_id, output_dir.name)
    except Exception as exc:
        log.warning("msg %d: credentials/domain aggregation failed: %s", msg_id, exc)

    uploaded = False
    if UPLOAD_URL:
        uploaded = _upload_via_http(msg_id, output_dir)
    elif GDRIVE_REMOTE:
        uploaded = _upload_to_drive(msg_id, output_dir)

    try:
        n = ingest_records(msg_id, output_dir)
        log.info("msg %d: ingested %d credentials into SQLite DB", msg_id, n)
    except Exception as exc:
        log.warning("msg %d: DB ingestion failed: %s", msg_id, exc)

    if uploaded:
        for d in (source_dir, output_dir):
            try:
                if d.exists():
                    d.unlink() if d.is_file() else __import__("shutil").rmtree(d)
                    log.info("msg %d: cleaned up %s", msg_id, d)
            except Exception as exc:
                log.warning("msg %d: cleanup of %s failed: %s", msg_id, d, exc)


def _upload_via_http(msg_id: int, output_dir: Path) -> None:
    """Copy the parsed outputs (domain_hits.txt, summary.csv, records.jsonl, ...)
    to the configured UPLOAD_URL (a cloudflared tunnel to the user's PC) via HTTP
    PUT. Files larger than UPLOAD_CHUNK_MB are uploaded in Content-Range chunks
    so they stay under Cloudflare's ~100 MB request cap. Never raises."""
    import subprocess
    from urllib.parse import quote

    chunk_bytes = int(UPLOAD_CHUNK_MB * 1024 * 1024) if UPLOAD_CHUNK_MB else 0

    files = sorted(f for f in output_dir.rglob("*")
                   if f.is_file() and f.name in ("credentials.txt", "domain_hits.txt"))
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
                            ["curl", "-sS", "-f", "-X", "PUT", "-H", f"Content-Range: {header}",
                             "--data-binary", "@-", dest],
                            input=chunk, capture_output=True, timeout=900,
                        )
                        if proc.returncode != 0:
                            err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
                            log.warning("msg %d: upload chunk of %s failed (rc=%d): %s",
                                        msg_id, rel, proc.returncode, err)
                            break
                        sent += len(chunk)
                if sent >= size:
                    ok += 1
                continue
            proc = subprocess.run(
                ["curl", "-sS", "-f", "-T", str(f), dest],
                capture_output=True, text=True, timeout=900,
            )
        except Exception as exc:
            log.warning("msg %d: upload of %s failed to run: %s", msg_id, rel, exc)
            continue
        if proc.returncode == 0:
            ok += 1
        else:
            log.warning("msg %d: upload of %s failed (rc=%d): %s",
                        msg_id, rel, proc.returncode, (proc.stderr or "").strip()[:200])
    if ok:
        log.info("msg %d: uploaded %d/%d files to %s", msg_id, ok, len(files), UPLOAD_URL)
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
            capture_output=True, text=True, timeout=300,
        )
    except Exception as exc:
        log.warning("msg %d: drive upload failed to run: %s", msg_id, exc)
        return False
    if proc.returncode != 0:
        log.warning("msg %d: drive upload failed (rc=%d): %s",
                    msg_id, proc.returncode, (proc.stderr or "").strip()[:500])
        return False
    log.info("msg %d: uploaded parsed outputs to %s", msg_id, dest)
    return True


def schedule_parse(msg_id: int, source_dir: Path, output_dir: Path) -> None:
    if not PARSE_LOGS:
        return
    task = asyncio.get_running_loop().create_task(_parse_in_background(msg_id, source_dir, output_dir))
    _parse_tasks.add(task)
    task.add_done_callback(_parse_tasks.discard)


def load_state() -> set:
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8"))["processed"])
    except Exception:
        return set()


def save_state(processed: set) -> None:
    ids = sorted(processed)[-MAX_STATE:]
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
    raise SystemExit(f"Could not resolve channel '{ref}'. Run make_session.py to list your channels.")


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
            if parsed.hostname not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
                continue
            bot = parsed.path.strip("/").split("/", 1)[0].lstrip("@").lower()
            payload = parse_qs(parsed.query).get("start", [None])[0]
            if bot in DOWNLOAD_BOTS and payload:
                return bot, payload
    return None


async def request_bot_document(client: TelegramClient, bot: str, payload: str, lock: asyncio.Lock):
    log.info("requesting file from @%s", bot)
    async with lock:
        deadline = monotonic() + BOT_RESPONSE_TIMEOUT
        async with client.conversation(bot, timeout=BOT_RESPONSE_TIMEOUT, exclusive=True) as conv:
            await conv.send_message(f"/start {payload}")
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                response = await asyncio.wait_for(conv.get_response(), timeout=remaining)
                if response.document:
                    return response


async def process_message(client: TelegramClient, msg, processed: set, bot_lock: asyncio.Lock) -> None:
    if msg.id in processed:
        return

    source = msg
    fname = document_name(source) if source.document else None
    deep_link = None if fname else bot_deep_link(msg)
    if not fname and not deep_link:
        return

    candidates = password_candidates(getattr(msg, "message", "") or "")
    if not candidates:
        log.warning("msg %d: no '.pass:' line found in post - leaving pending", msg.id)
        return

    if deep_link:
        bot, payload = deep_link
        try:
            source = await request_bot_document(client, bot, payload, bot_lock)
        except asyncio.TimeoutError:
            log.error("msg %d: timed out waiting for @%s to return a file (attempt %d/%d)",
                      msg.id, bot, _failures.get(str(msg.id), {}).get("count", 0) + 1, MAX_RETRIES)
            if record_failure(msg.id, f"bot_timeout:{bot}"):
                log.warning("msg %d: abandoning after %d failed bot attempts - marked processed", msg.id, MAX_RETRIES)
                processed.add(msg.id)
                save_state(processed)
            return
        except Exception as e:
            log.error("msg %d: @%s file request failed: %s", msg.id, bot, e)
            if record_failure(msg.id, f"bot_error:{e}"):
                log.warning("msg %d: abandoning after %d failures - marked processed", msg.id, MAX_RETRIES)
                processed.add(msg.id)
                save_state(processed)
            return
        fname = document_name(source)
        if not fname:
            log.error("msg %d: @%s returned an unsupported or unnamed document", msg.id, bot)
            return

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / f"{msg.id}_{fname}"
    dest = DOWNLOAD_DIR / f"{msg.id}_{Path(fname).stem}"

    if dest.exists() and any(dest.rglob("*")):
        log.info("msg %d: extracted dir already exists at %s - skipping download/extraction", msg.id, dest)
    elif not (target.exists() and target.stat().st_size > 0):
        size_mb = (source.document.size or 0) / 1e6
        log.info("msg %d: downloading %s (%.1f MB)", msg.id, fname, size_mb)
        last = {"pct": -1}

        def progress(cur, total):
            pct = int(cur * 100 / total) if total else 0
            if pct >= last["pct"] + 5:
                last["pct"] = pct
                log.info("msg %d: %d%% (%.1f/%.1f MB)", msg.id, pct, cur / 1e6, total / 1e6)

        downloaded = await client.download_media(source, file=str(target), progress_callback=progress)
        if not downloaded or not target.exists() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            log.error("msg %d: Telegram did not return file data; leaving it pending for retry", msg.id)
            return

        try:
            used = extract_archive(target, candidates, dest)
        except Exception as e:
            log.error("msg %d: extraction failed (%s). Archive kept at: %s (attempt %d/%d)",
                      msg.id, e, target,
                      _failures.get(str(msg.id), {}).get("count", 0) + 1, MAX_RETRIES)
            if record_failure(msg.id, f"extraction:{e}"):
                log.warning("msg %d: abandoning after %d extraction failures - marked processed", msg.id, MAX_RETRIES)
                processed.add(msg.id)
                save_state(processed)
                target.unlink(missing_ok=True)
            return
        log.info("msg %d: extracted %s (pw ok) -> %s", msg.id, fname, dest)
        target.unlink(missing_ok=True)

    processed.add(msg.id)
    save_state(processed)
    clear_failure(msg.id)
    parse_out = DOWNLOAD_DIR / f"{msg.id}_{Path(fname).stem}.parsed"
    schedule_parse(msg.id, dest, parse_out)


async def catch_up(client: TelegramClient, entity, hours: float, processed: set,
                   bot_lock: asyncio.Lock, label: str) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    log.info("%s: processing posts newer than %s UTC", label, cutoff.strftime("%Y-%m-%d %H:%M"))
    async for msg in client.iter_messages(entity, offset_date=cutoff, reverse=True):
        await process_message(client, msg, processed, bot_lock)
    log.info("%s: done", label)


async def run(args) -> None:
    if not (API_ID and API_HASH):
        sys.exit("Set TG_API_ID / TG_API_HASH in .env (get them from https://my.telegram.org). See README.")
    if not CHANNEL:
        sys.exit("Set CHANNEL in .env (e.g. @channelname, https://t.me/channelname, or numeric id).")
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
    log.info("watching channel: %s | downloads -> %s", title, DOWNLOAD_DIR.resolve())

    backfill = args.backfill_hours if args.backfill_hours is not None else (BACKFILL_HOURS if fresh else 0)
    if backfill > 0:
        await catch_up(client, entity, backfill, processed, bot_lock, "backfill")

    sweep = args.sweep_hours if args.sweep_hours is not None else SWEEP_HOURS
    if sweep > 0:
        await catch_up(client, entity, sweep, processed, bot_lock, "sweep")

    @client.on(events.NewMessage(chats=entity))
    @client.on(events.MessageEdited(chats=entity))
    async def on_message(event):
        await process_message(client, event.message, processed, bot_lock)

    log.info("live: waiting for new or edited posts (Ctrl+C to stop)")
    try:
        await client.run_until_disconnected()
    finally:
        if _parse_tasks:
            pending = tuple(_parse_tasks)
            log.info("waiting for %d background parse task(s) to finish...", len(pending))
            await asyncio.gather(*pending, return_exceptions=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill-hours", type=float, default=None,
                    help="once: process posts from the last N hours (0=off; default: 24 on first run only)")
    ap.add_argument("--sweep-hours", type=float, default=None,
                    help="catch-up window checked on every start (default: 2)")
    args = ap.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
