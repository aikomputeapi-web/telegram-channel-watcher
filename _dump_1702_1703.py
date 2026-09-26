"""Dump full text of msgs 1702/1703."""
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
    msgs = await client.get_messages(ent, ids=[1702, 1703])
    for m in msgs:
        print("=" * 70)
        print(f"msg_id={m.id} date={m.date} file={(m.file.name if m.file else None)!r}")
        print(ascii(m.text))
        print(f"--- buttons: {[ [b.url for b in row if getattr(b,'url',None)] for row in (m.buttons or []) ]}")
    await client.disconnect()

import asyncio
asyncio.run(check())
