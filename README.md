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

Posts may also contain an allowlisted Telegram bot deep link such as
`https://t.me/boxedrobot?start=MTg2NA==`. The watcher sends the equivalent
`/start MTg2NA==` command to that bot, waits for its document, then downloads
and extracts it using the `.pass:` value from the original channel post.

## What you get

- `downloads/<msg_id>_<archive name>/…` — extracted contents
- the original archive is deleted after successful extraction
- if the optional `stealer-log-parser` package is installed, each extracted
  archive is also parsed **in the background**: `records.jsonl`, `summary.csv`,
  `report.md`, `manifest.json`, `diagnostics.jsonl` and a per-archive
  `domain_hits.txt` (`domain : hits` per line, from credential/autofill
  origins) appear in `downloads/<msg_id>_<name>.parsed/`. Parsing never blocks
  the watcher. Very large extractions are split into size-bounded batches
  (`PARSE_BATCH_MB`, `PARSE_BATCH_FILES`) so peak memory stays predictable, and
  batches over `PARSE_MAX_TOTAL_MB` / `PARSE_MAX_FILES` are skipped.
- If `UPLOAD_URL` is set (a cloudflared quick tunnel to a machine running
  `upload_receiver.py`), all parsed outputs are uploaded there automatically
  after each archive finishes parsing.
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
| `DOWNLOAD_BOTS` | `boxedrobot` | comma-separated bot usernames whose `?start=` links may be followed |
| `BOT_RESPONSE_TIMEOUT` | `120` | seconds to wait for a download bot's document |
| `SEVENZIP` | auto | explicit path to `7z`/`7zz` if auto-detection fails |
| `PARSE_LOGS` | `1` | set `0` to disable parsing of extracted archives |
| `PARSER_REDACTION` | `none` | `none`/`partial`/`full` redaction in parser output |
| `PARSER_FAMILY` | `auto` | force a stealer family, or let the parser detect it |
| `PARSE_MAX_TOTAL_MB` | `6144` | skip parsing extractions larger than this many MB |
| `PARSE_MAX_FILES` | `300000` | skip parsing extractions with more files than this |
| `PARSE_BATCH_MB` | `512` | parse large extractions in ~this many MB per batch |
| `PARSE_BATCH_FILES` | `4000` | parse large extractions in batches of ~this many files |
| `UPLOAD_URL` | — | cloudflared tunnel URL; parsed outputs are uploaded to it after each parse |

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
- Log parsing is optional. Install the parser next to this repo and it is picked
  up automatically:

  ```
  git clone <your/stealer-log-parser-repo> ..\stealer-log-parser
  ..\venv\Scripts\pip install -e ..\stealer-log-parser     (Windows)
  ```

- Telegram's normal cloud download icon does not need to be clicked first;
  Telethon downloads the document directly. Only `?start=` links for bots in
  `DOWNLOAD_BOTS` are followed; other inline buttons are ignored.
- Zip (standard + AES-256) extraction is pure Python (`pyzipper`); `.7z`/`.rar`
  need the 7-Zip binary (auto-detected: `7zz`/`7z`/`7za`/Program Files, or set
  `SEVENZIP`).
- This uses MTProto with your account — a normal, logged-in client. Keep the
  session string private (it grants account access); you can kill it any time
  via Telegram Settings → Devices.
- Don't run the same account's watcher in two places at once; if you do, keep
  separate `STATE_FILE`/`DOWNLOAD_DIR` and expect duplicate downloads.
