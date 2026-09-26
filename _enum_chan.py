"""Enumerate OBSERVERNEVERDIE posts since Sept 11 to compute catch-up needs."""
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

s = open(r"C:\Users\Administrator\coding\telegram-channel-watcher\_session_string.txt").read().strip()
api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")

async def check():
    client = TelegramClient(StringSession(s), api_id, api_hash)
    await client.connect()
    ent = await client.get_entity("OBSERVERNEVERDIE")
    print(f"channel: {ent.title} marked=-100{ent.id}")
    # window: since 2026-09-10 00:00 UTC, oldest first
    from datetime import datetime, timezone
    cutoff = datetime(2026, 9, 10, tzinfo=timezone.utc)
    n = 0
    async for m in client.iter_messages(ent, offset_date=None, reverse=True):
        if m.date < cutoff:
            continue
        n += 1
        doc = m.file.name if m.file else None
        kind = "DOC" if m.document else ("PHOTO" if m.photo else "TXT")
        print(f"msg_id={m.id} date={m.date:%m-%d %H:%M} kind={kind} file={ascii(doc) if doc else ''}")
    print(f"total msgs since Sept 10: {n}")
    await client.disconnect()

import asyncio
asyncio.run(check())
