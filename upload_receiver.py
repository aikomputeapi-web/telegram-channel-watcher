"""HTTP upload receiver for the telegram-channel-watcher.

Listens on a local port, accepts multipart/file uploads (POST/PUT), and saves
them under a target directory. Expose it to the VM with cloudflared:

    cloudflared tunnel --url http://localhost:8787

Then the VM pushes parsed outputs (domain_hits.txt, records.jsonl, ...) to it.
"""
import argparse
import http.server
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

SAFE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize(name: str) -> str:
    name = Path(name).name
    name = SAFE.sub("_", name).strip("._")
    return name or "unnamed"


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "tg-watcher-receiver/1.0"

    def _save_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        fname = "upload"
        # Preserve the directory structure from the URL path.
        path = urllib.parse.urlparse(self.path).path
        parts = [sanitize(urllib.parse.unquote(p)) for p in path.strip("/").split("/") if p.strip("/")]
        # Fall back to Content-Disposition filename when the URL has no useful name.
        cd = self.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        if not parts:
            if m:
                fname = sanitize(m.group(1))
            parts = [fname]
        elif m and parts[-1] in ("upload", ""):
            parts[-1] = sanitize(m.group(1))
        rel = Path(*parts) if parts else Path("upload")
        target = Path(self.server.upload_dir) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # Support resumable chunked uploads via Content-Range.
        cr = self.headers.get("Content-Range", "")
        m = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", cr)
        if m:
            start = int(m.group(1))
            mode = "r+b" if target.exists() else "w+b"
            with target.open(mode) as fh:
                fh.seek(start)
                fh.write(body)
            fname = rel.as_posix()
            return fname, len(body), int(m.group(2)) + 1 >= length
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            target = target.with_name(f"{target.stem}_{stamp}{target.suffix}")
        target.write_bytes(body)
        fname = target.name
        return fname, len(body), True

    def _handle(self):
        try:
            fname, size, done = self._save_body()
        except Exception as exc:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(exc).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"saved {fname} (+{size} bytes, done={done})".encode())
        if done:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] complete {fname} ({size} bytes chunk)")

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def log_message(self, fmt, *args):
        pass


class UploadServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, upload_dir: Path):
        self.upload_dir = upload_dir
        super().__init__(addr, Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--upload-dir", type=str,
                    default=str(Path.home() / "Downloads" / "tg-watcher" / "uploads"))
    args = ap.parse_args()

    upload_dir = Path(args.upload_dir).expanduser().resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    server = UploadServer(("127.0.0.1", args.port), upload_dir)
    print(f"listening on http://127.0.0.1:{args.port} -> {upload_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
