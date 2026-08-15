"""Smoke tests for Telegram message classification (no network needed)."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watcher import bot_deep_link, document_name, request_bot_document


def message_with_url(url: str):
    button = SimpleNamespace(url=url)
    return SimpleNamespace(buttons=[[button]])


class FakeConversation:
    def __init__(self):
        self.sent = []
        self.responses = [SimpleNamespace(document=None), SimpleNamespace(document=object())]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def send_message(self, text: str):
        self.sent.append(text)

    async def get_response(self):
        return self.responses.pop(0)


class FakeClient:
    def __init__(self):
        self.conv = FakeConversation()
        self.requested_bot = None

    def conversation(self, bot, **kwargs):
        self.requested_bot = bot
        return self.conv


async def test_bot_request() -> None:
    client = FakeClient()
    response = await request_bot_document(client, "boxedrobot", "MTg2NA==", asyncio.Lock())
    assert client.requested_bot == "boxedrobot"
    assert client.conv.sent == ["/start MTg2NA=="]
    assert response.document is not None


def main() -> None:
    assert bot_deep_link(message_with_url("https://t.me/boxedrobot?start=MTg2NA==")) == (
        "boxedrobot",
        "MTg2NA==",
    )
    assert bot_deep_link(message_with_url("https://t.me/untrustedbot?start=payload")) is None
    assert bot_deep_link(message_with_url("https://example.com/?start=payload")) is None

    archive = SimpleNamespace(file=SimpleNamespace(name="folder/pack.ZIP"))
    unnamed = SimpleNamespace(file=SimpleNamespace(name=None))
    unsupported = SimpleNamespace(file=SimpleNamespace(name="video.mp4"))
    assert document_name(archive) == "pack.ZIP"
    assert document_name(unnamed) is None
    assert document_name(unsupported) is None

    asyncio.run(test_bot_request())

    print("WATCHER TESTS PASSED")


if __name__ == "__main__":
    main()
