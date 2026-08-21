#!/usr/bin/env python3
"""Read-only report of recent channel posts and watcher eligibility."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv(Path(__file__).parent / ".env")


async def main():
    client = TelegramClient(
        StringSession(os.environ["TG_SESSION_STRING"]),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    await client.connect()
    ref = os.environ["CHANNEL"]
    entity = await client.get_entity(int(ref) if ref.lstrip("-").isdigit() else ref)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=168)
    counts = {"total": 0, "document": 0, "archive": 0, "password": 0, "buttons": 0, "deep_link_bot": 0}
    print(f"channel={getattr(entity, 'title', ref)!r} id={ref} cutoff={cutoff.isoformat()}")
    async for msg in client.iter_messages(entity):
        if msg.date < cutoff:
            break
        counts["total"] += 1
        name = Path(msg.file.name).name if msg.file and msg.file.name else "-"
        suffix = Path(name).suffix.lower()
        archive = suffix in {".zip", ".7z", ".rar"}
        has_password = ".pass" in (msg.message or "").lower()
        buttons = []
        for row in msg.buttons or []:
            for button in row:
                url = getattr(button, "url", None)
                if url:
                    parsed = urlparse(url)
                    buttons.append(
                        f"{parsed.hostname}{parsed.path} query_keys={sorted(parse_qs(parsed.query))}"
                    )
        deep_link = False
        for b in buttons:
            if "boxedrobot" in b or ("start" in b and "t.me" in b):
                deep_link = True
                break
        counts["document"] += bool(msg.document)
        counts["archive"] += archive
        counts["password"] += has_password
        counts["buttons"] += bool(buttons)
        counts["deep_link_bot"] += deep_link
        print(
            f"id={msg.id} date={msg.date.isoformat()} document={bool(msg.document)} "
            f"archive={archive} pass={has_password} name={name!r} buttons={buttons} deep_link_bot={deep_link}"
        )
    print("counts=" + repr(counts))
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
