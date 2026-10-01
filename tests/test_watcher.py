"""Smoke tests for Telegram message classification (no network needed)."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watcher import _dir_metrics, bot_deep_link, document_name, request_bot_document


def message_with_url(url: str):
    button = SimpleNamespace(url=url)
    return SimpleNamespace(buttons=[[button]])


class FakeClient:
    """Fake matching the event-catcher flow: registers handlers, sends the
    /start, and delivers queued fake bot messages."""

    def __init__(self):
        self.handlers = []
        self.sent = []
        self.forwarded = []
        self.queue_task = None

    def add_event_handler(self, cb, evt=None):
        self.handlers.append(cb)

    def remove_event_handler(self, cb, evt=None):
        if cb in self.handlers:
            self.handlers.remove(cb)

    async def send_message(self, ent, text):
        self.sent.append(text)
        # deliver two fake bot replies: a service note, then the file message
        for msg in self._responses:
            await asyncio.sleep(0.01)
            cb = self.handlers[-1]
            cb(SimpleNamespace(message=msg))

    async def forward_messages(self, dest, msg, silent=False):
        self.forwarded.append(msg)
        return SimpleNamespace(id=999, media=msg.media)

    async def get_entity(self, bot):
        return f"entity:{bot}"

    _responses = [
        SimpleNamespace(document=None, media=None, message="please wait..."),
        SimpleNamespace(document=object(), media=object(), message="here is your file"),
    ]


async def test_bot_request() -> None:
    client = FakeClient()
    response = await request_bot_document(client, "boxedrobot", "MTg2NA==", asyncio.Lock())
    assert client.sent == ["/start MTg2NA=="], client.sent
    assert len(client.forwarded) == 1, "file message was not forwarded to Saved Messages"
    assert response.media is not None, "returned the durable Saved copy"
    # handler must be unregistered after the request completes
    assert not client.handlers, "event handler leaked"


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

    # _dir_metrics sums bytes + counts files under a root
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.txt").write_text("x" * 100)
        sub = root / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("y" * 50)
        (sub / "c.bin").write_bytes(b"z" * 10)
        total, count = _dir_metrics(root)
        assert (total, count) == (160, 3)

    asyncio.run(test_bot_request())

    print("WATCHER TESTS PASSED")


if __name__ == "__main__":
    main()
