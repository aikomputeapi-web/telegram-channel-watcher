"""Probe what channel refs resolve with the new session."""
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel

load_dotenv()

s = open(r"C:\Users\Administrator\coding\telegram-channel-watcher\_session_string.txt").read().strip()
api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")

async def check():
    client = TelegramClient(StringSession(s), api_id, api_hash)
    await client.connect()

    for ref in ["OBSERVERNEVERDIE", "@OBSERVERCLOUD"]:
        try:
            ent = await client.get_entity(ref)
            print(f"RESOLVED {ref!r}: id={ent.id} marked={-1000000000000 - ent.id if getattr(ent,'id',0)>0 else '?'} title={ascii(getattr(ent,'title',None))} username={getattr(ent,'username',None)}")
            try:
                msgs = await client.get_messages(ent, limit=2)
                for m in msgs:
                    print(f"   msg_id={m.id} date={m.date}")
            except Exception as e:
                print(f"   read failed: {e}")
        except Exception as e:
            print(f"FAILED {ref!r}: {e}")

    for cand in [-1001833254475, PeerChannel(1833254475)]:
        try:
            ent = await client.get_entity(cand)
            print(f"RESOLVED {cand!r}: id={ent.id} title={ascii(getattr(ent,'title',None))} username={getattr(ent,'username',None)}")
        except Exception as e:
            print(f"FAILED {cand!r}: {type(e).__name__} {e}")

    await client.disconnect()

import asyncio
asyncio.run(check())
