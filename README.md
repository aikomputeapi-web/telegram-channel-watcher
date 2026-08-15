# telegram-channel-watcher

Watches a Telegram channel with **your own user account** (no bot, no admin
rights needed — you just have to be a member of the channel). For every post
containing an archive (`.zip` / `.7z` / `.rar`) plus a password line like
`.pass: secret123`, it downloads the file, extracts it with that password, and
deletes the archive. No file size limit (up to 4 GB per Telegram file).

## Setup (one time, ~3 minutes)

1. **API credentials** — log in at <https://my.telegram.org> → *API development
   tools* → create an app → copy `api_id` and `api_hash`.

2. **Configure** — copy `.env.example` to `.env` and fill in `TG_API_ID` and
   `TG_API_HASH`.

3. **Log in your account** (interactive, once):

   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt      (Windows)
   python make_session.py
   ```

   Enter phone number + the login code Telegram sends you. It prints:
   - a **session string** → put it in `.env` as `TG_SESSION_STRING`
   - your channel list → pick the id / @username for `CHANNEL` in `.env`

4. **Run**:

   ```
   .venv\Scripts\python -u watcher.py                 (Windows)
   ```

On the first run it backfills the last **24 h** of posts (`BACKFILL_HOURS`),
then watches live. On every later start it re-checks the last **2 h**
(`SWEEP_HOURS`) to catch anything posted while it was down.

New posts and edits are both watched. If Telegram adds the archive or the
`.pass:` caption in a later edit, the watcher leaves the post pending and
processes the completed version.

## What you get

- `downloads/<msg_id>_<archive name>/…` — extracted contents
- the original archive is deleted after successful extraction
- `state.json` — remembers processed posts (no re-downloads across restarts)
- failed extractions (e.g. wrong/missing password) keep the archive in
  `downloads/` and are retried on the next restart sweep

## Config (`.env`)

| Var | Default | Meaning |
| --- | --- | --- |
| `TG_API_ID` / `TG_API_HASH` | — | from my.telegram.org (required) |
| `TG_SESSION_STRING` | — | from `make_session.py` (recommended for VPS) |
| `CHANNEL` | — | `@name`, `t.me/name`, `t.me/c/123…`, or `-100…` id (required) |
| `DOWNLOAD_DIR` | `downloads` | where files land |
| `BACKFILL_HOURS` | `24` | one-time catch-up window on first run |
| `SWEEP_HOURS` | `2` | catch-up window checked on every start |
| `SEVENZIP` | auto | explicit path to `7z`/`7zz` if auto-detection fails |

## Deploying to a VPS

### systemd (any Linux box)

```
sudo mkdir -p /opt/tg-watcher && sudo chown $USER /opt/tg-watcher
cp watcher.py extractor.py requirements.txt .env /opt/tg-watcher/
cd /opt/tg-watcher
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo apt install 7zip p7zip-full        # for .7z / .rar
sudo cp deploy/tg-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tg-watcher
journalctl -u tg-watcher -f             # watch the logs
```

### Docker

```
docker build -t tg-watcher .
docker run -d --restart unless-stopped --env-file .env -v tgdata:/data tg-watcher
```

Railway/etc. also works: set the env vars, no `CHANNEL` dialog-scan needed if
you use the numeric channel id or @username.

## Notes & limits

- The `.pass:` line must be in the **same message** as the file. For Telegram
  albums (multiple files grouped under one caption) only the message that
  actually carries the caption text is processed.
- Telegram's normal cloud download icon does not need to be clicked first;
  Telethon downloads the document directly. Inline bot buttons are not clicked
  automatically.
- Zip (standard + AES-256) extraction is pure Python (`pyzipper`); `.7z`/`.rar`
  need the 7-Zip binary (auto-detected: `7zz`/`7z`/`7za`/Program Files, or set
  `SEVENZIP`).
- This uses MTProto with your account — a normal, logged-in client. Keep the
  session string private (it grants account access); you can kill it any time
  via Telegram Settings → Devices.
- Don't run the same account's watcher in two places at once; if you do, keep
  separate `STATE_FILE`/`DOWNLOAD_DIR` and expect duplicate downloads.
