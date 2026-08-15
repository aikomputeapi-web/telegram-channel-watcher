#!/usr/bin/env python3
"""One-time interactive Telegram login.

Run this locally with your API credentials (TG_API_ID / TG_API_HASH in .env,
or it will prompt). After entering phone + login code it prints:

  1. a TG_SESSION_STRING  -> put it in .env / your VPS environment
  2. your channels        -> pick the numeric id or @username for CHANNEL
"""
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()


def main():
    api_id = os.getenv("TG_API_ID") or input("TG_API_ID: ").strip()
    api_hash = os.getenv("TG_API_HASH") or input("TG_API_HASH: ").strip()
    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        session_string = client.session.save()
        print("\n--- put this in .env as TG_SESSION_STRING ---")
        print(session_string)
        print("--- your channels (use id or @username for CHANNEL) ---")
        for dlg in client.iter_dialogs():
            if dlg.is_channel:
                username = getattr(dlg.entity, "username", None)
                uname = f"@{username}" if username else "-"
                print(f"  {dlg.id:>15}  {uname:<25} {dlg.name}")


if __name__ == "__main__":
    main()
