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

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from extractor import extract_archive, is_supported_archive, password_candidates

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
MAX_STATE = 20000

log = logging.getLogger("watcher")


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


async def process_message(client: TelegramClient, msg, processed: set) -> None:
    if msg.id in processed or not msg.document:
        return
    fname = msg.file.name if msg.file else None
    if not fname:
        return
    fname = Path(fname).name
    if not is_supported_archive(fname):
        return

    candidates = password_candidates(getattr(msg, "message", "") or "")
    if not candidates:
        log.warning("msg %d (%s): no '.pass:' line found in post - skipping", msg.id, fname)
        processed.add(msg.id)
        save_state(processed)
        return

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / f"{msg.id}_{fname}"
    if not (target.exists() and target.stat().st_size > 0):
        size_mb = (msg.document.size or 0) / 1e6
        log.info("msg %d: downloading %s (%.1f MB)", msg.id, fname, size_mb)
        last = {"pct": -1}

        def progress(cur, total):
            pct = int(cur * 100 / total) if total else 0
            if pct >= last["pct"] + 5:
                last["pct"] = pct
                log.info("msg %d: %d%% (%.1f/%.1f MB)", msg.id, pct, cur / 1e6, total / 1e6)

        await client.download_media(msg, file=str(target), progress_callback=progress)

    dest = DOWNLOAD_DIR / f"{msg.id}_{Path(fname).stem}"
    try:
        used = extract_archive(target, candidates, dest)
    except Exception as e:
        # Not marked processed -> retried on the next startup sweep.
        log.error("msg %d: extraction failed (%s). Archive kept at: %s", msg.id, e, target)
        return
    log.info("msg %d: extracted %s (pw ok) -> %s", msg.id, fname, dest)
    target.unlink(missing_ok=True)
    processed.add(msg.id)
    save_state(processed)


async def catch_up(client: TelegramClient, entity, hours: float, processed: set, label: str) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    log.info("%s: processing posts newer than %s UTC", label, cutoff.strftime("%Y-%m-%d %H:%M"))
    async for msg in client.iter_messages(entity, offset_date=cutoff, reverse=True):
        await process_message(client, msg, processed)
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
    fresh = not STATE_FILE.exists()
    log.info("watching channel: %s | downloads -> %s", title, DOWNLOAD_DIR.resolve())

    backfill = args.backfill_hours if args.backfill_hours is not None else (BACKFILL_HOURS if fresh else 0)
    if backfill > 0:
        await catch_up(client, entity, backfill, processed, "backfill")

    sweep = args.sweep_hours if args.sweep_hours is not None else SWEEP_HOURS
    if sweep > 0:
        await catch_up(client, entity, sweep, processed, "sweep")

    @client.on(events.NewMessage(chats=entity))
    async def on_new(event):
        await process_message(client, event.message, processed)

    log.info("live: waiting for new posts (Ctrl+C to stop)")
    await client.run_until_disconnected()


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
