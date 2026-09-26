"""Validate channel access using PeerChannel from fresh StringSession."""
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel

load_dotenv()

s = open(r"C:\Users\Administrator\coding\telegram-channel-watcher\_session_string.txt").read().strip()
api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")
chan = int(os.getenv("CHANNEL").strip())

async def check():
    client = TelegramClient(StringSession(s), api_id, api_hash)
    await client.connect()
    # fresh StringSession has no entity cache: populate via dialogs first
    found = None
    marked = int(f"-100{chan}")
    count = 0
    async for d in client.iter_dialogs():
        count += 1
        ent = d.entity
        kind = type(ent).__name__
        title = getattr(ent, "title", None) or getattr(ent, "username", None) or ""
        print(f"  dialog: id={d.id} kind={kind} title={ascii(title)}")
        if d.id in (chan, marked) or getattr(ent, "channel_id", None) == chan:
            found = d
    print(f"total dialogs: {count}")
    if not found:
        print(f"FATAL: channel {chan} not found in account dialogs")
        await client.disconnect()
        import sys
        sys.exit(1)
    ent = found.entity
    print(f"channel OK: {getattr(ent, 'title', '?')} id={ent.id}")
    msgs = await client.get_messages(ent, limit=3)
    for m in msgs:
        print(f"  msg_id={m.id}  date={m.date}  media={type(m.media).__name__ if m.media else None}")
    await client.disconnect()
    print("CHANNEL VALIDATION OK")

import asyncio
asyncio.run(check())
