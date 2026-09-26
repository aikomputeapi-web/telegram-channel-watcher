#!/usr/bin/env python3
"""Interactive Telegram login v2 - handles 2FA, flood-wait, bad codes.

Run from the project dir so .env supplies TG_API_ID / TG_API_HASH / CHANNEL.
Prints TG_SESSION_STRING between markers at the end.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

load_dotenv()


async def main():
    api_id = os.getenv("TG_API_ID") or input("TG_API_ID: ").strip()
    api_hash = os.getenv("TG_API_HASH") or input("TG_API_HASH: ").strip()
    channel = os.getenv("CHANNEL", "").strip()
    print(f"(target CHANNEL from .env: {channel or 'not set'})")

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        phone = input("Phone number (intl format, e.g. +15551234567): ").strip()
        try:
            await client.send_code_request(phone)
        except errors.FloodWaitError as e:
            print(f"Telegram flood-wait: must wait {e.seconds}s before requesting another code.")
            print("Note: previous code(s) may still be valid - just enter the newest one you got.")
            await client.disconnect()
            sys.exit(2)
        print("Code sent. Check Telegram app (Telegram service message) or SMS.")
        code = input("Login code: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except errors.SessionPasswordNeededError:
            print("2FA is enabled - enter your Telegram cloud password.")
            pw = input("Password: ").strip()
            await client.sign_in(password=pw)
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
            print(f"CODE REJECTED ({type(e).__name__}).")
            print("This happens if an older code was entered, or too much time passed.")
            print("Request a NEW code by running this script again, then enter the newest code.")
            await client.disconnect()
            sys.exit(3)
    else:
        print("already authorized")

    me = await client.get_me()
    print(f"\nsigned in as: {me.first_name} (@{me.username}) id={me.id}")
    s = client.session.save()
    if isinstance(s, bytes):
        s = s.decode()
    print("\n---TG_SESSION_STRING_START---")
    print(s)
    print("---TG_SESSION_STRING_END---")
    print(f"len={len(s)}")

    if channel:
        try:
            ent = await client.get_entity(int(channel) if channel.isdigit() else channel)
            print(f"channel OK: {getattr(ent, 'title', '?')} (id {ent.id})")
        except Exception as e:
            print(f"channel lookup failed: {e}")

    await client.disconnect()
    print("\nAll done. Copy the session string above back to Jcode.")


if __name__ == "__main__":
    asyncio.run(main())
