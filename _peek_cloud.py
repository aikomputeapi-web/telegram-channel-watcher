"""Peek @Everlasting_Cloud for the password covering @everlasting_cloud_3.zip."""
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
    try:
        ent = await client.get_entity("Everlasting_Cloud")
    except Exception as e:
        print(f"resolve failed: {e}")
        await client.disconnect()
        return
    print(f"channel: {ascii(getattr(ent,'title','?'))} id={ent.id} username={getattr(ent,'username',None)}")
    msgs = await client.get_messages(ent, limit=15)
    for m in msgs:
        doc = m.file.name if m.file else None
        text = ascii((m.text or "")[:120])
        print(f"msg_id={m.id} date={m.date:%m-%d %H:%M} doc={ascii(doc) if doc else ''} text={text}")
    await client.disconnect()

import asyncio
asyncio.run(check())
