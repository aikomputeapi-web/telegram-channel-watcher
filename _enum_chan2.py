"""Enumerate OBSERVERNEVERDIE msgs 1590-1703 to map coverage vs ingested dumps."""
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
    ids = list(range(1590, 1704))
    msgs = await client.get_messages(ent, ids=ids)
    n = 0
    for m in msgs:
        if m is None:
            continue
        n += 1
        doc = m.file.name if m.file else None
        kind = "DOC" if m.document else ("PHOTO" if m.photo else "TXT")
        text = (m.text or "")[:60].replace("\n", " ")
        print(f"msg_id={m.id} date={m.date:%m-%d} kind={kind} file={ascii(doc) if doc else ''} text={ascii(text)}")
    print(f"existing msgs in 1590-1703: {n}")
    await client.disconnect()

import asyncio
asyncio.run(check())
