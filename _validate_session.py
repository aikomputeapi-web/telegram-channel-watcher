"""Validate the new TG session string locally before deploying."""
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

s = open(r"C:\Users\Administrator\coding\telegram-channel-watcher\_session_string.txt").read().strip()
print(f"string length: {len(s)}")

api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")
channel = os.getenv("CHANNEL", "").strip()
print(f"api_id={api_id}  channel={channel}")

async def check():
    client = TelegramClient(StringSession(s), api_id, api_hash)
    await client.connect()
    ok = await client.is_user_authorized()
    print(f"authorized: {ok}")
    if not ok:
        print("FATAL: session string did not authorize")
        sys.exit(1)
    me = await client.get_me()
    print(f"me: {me.first_name} (@{me.username}) id={me.id}")
    try:
        ent = await client.get_entity(int(channel) if channel.isdigit() else channel)
        print(f"channel OK: {getattr(ent, 'title', '?')} (id {ent.id})")
        # peek at latest message id to report catch-up state
        msgs = await client.get_messages(ent, limit=1)
        if msgs:
            print(f"latest channel msg_id: {msgs[0].id}  date: {msgs[0].date}")
    except Exception as e:
        print(f"channel lookup failed: {e}")
        sys.exit(2)
    await client.disconnect()
    print("VALIDATION OK")

import asyncio
asyncio.run(check())
