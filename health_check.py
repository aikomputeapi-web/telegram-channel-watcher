#!/usr/bin/env python3
"""Health monitor for the tg-watcher stack on the VPS.

Runs from a systemd timer (every 15 min) and checks:
  1. free disk space            (CRIT < 3 GB or < 8%, WARN < 15%)
  2. tg-watcher + cred-search systemd services active
  3. tg-watcher crash-looping   ("Failed with result" >= 3 in last 2 h)
  4. app ERROR spam             (> 10 ERROR lines in tg-watcher journal, 2 h)
  5. state.json staleness       (no processing activity for 30 h)
  6. failures.json growth       (new failed msg ids since the last check)
  7. credentials DB reachable   (SELECT COUNT(*) works)

On any issue it gathers context (df, journal tail, failures.json, downloads
dir, service states), optionally asks Google Gemini for a root-cause
diagnosis, and sends a Telegram alert to the account owner's Saved Messages
('me') using the watcher's own session. Sends a recovery notice when issues
clear. Alerts are rate-limited via .health_state.json: CRIT every 30 min,
WARN every 6 h.

Usage:
  health_check.py            run checks (used by systemd timer)
  health_check.py --status   print a status summary, no alerts
  health_check.py --force    send a test alert to Telegram now
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/opt/tg-watcher")
DB_PATH = Path(os.getenv("CREDDB_DB_PATH", BASE / "credentials.db"))
HEALTH_STATE = BASE / ".health_state.json"
LOG_FILE = Path("/var/log/tg-health.log")
SERVICES = ("tg-watcher", "cred-search")
GEMINI_MODELS = ("gemini-2.0-flash", "gemini-flash-latest", "gemini-1.5-flash")

CRIT_RESEND_S = 30 * 60
WARN_RESEND_S = 6 * 3600

load_dotenv(BASE / ".env")

API_ID = os.getenv("TG_API_ID")
API_HASH = os.getenv("TG_API_HASH")
SESSION_STRING = os.getenv("TG_SESSION_STRING")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def run(cmd: list[str], timeout: int = 60) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return f"<command failed: {exc}>"


def load_health_state() -> dict:
    try:
        return json.loads(HEALTH_STATE.read_text())
    except Exception:
        return {}


def save_health_state(st: dict) -> None:
    tmp = HEALTH_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(HEALTH_STATE)


def log_line(text: str) -> None:
    try:
        with LOG_FILE.open("a") as fh:
            fh.write(f"{now_utc()} {text}\n")
    except OSError:
        pass


# ── checks ──────────────────────────────────────────────────────────────


def check_disk() -> tuple[str, str] | None:
    usage = shutil.disk_usage("/")
    free_gb = usage.free / 1024**3
    pct = usage.free / usage.total * 100
    if free_gb < 3 or pct < 8:
        return ("crit", f"disk nearly full: {free_gb:.1f} GB free ({pct:.0f}%)")
    if pct < 15:
        return ("warn", f"disk getting low: {free_gb:.1f} GB free ({pct:.0f}%)")
    return None


def check_services() -> list[tuple[str, str]]:
    issues = []
    for svc in SERVICES:
        if "active" not in run(["systemctl", "is-active", svc]).strip():
            issues.append(("crit", f"service {svc} not active"))
    return issues


def check_journal() -> list[tuple[str, str]]:
    issues = []
    journal = run(
        ["journalctl", "-u", "tg-watcher", "--since", "-2h", "--no-pager", "-n", "2000"]
    )
    crashes = journal.count("Failed with result")
    if crashes >= 3:
        issues.append(("crit", f"tg-watcher crash-looping: {crashes} failures in 2h"))
    errors = sum(1 for line in journal.splitlines() if " ERROR " in line or " ERROR\t" in line)
    if errors > 10:
        issues.append(("warn", f"{errors} ERROR lines in tg-watcher journal (2h)"))
    return issues, journal


def check_state_staleness() -> tuple[str, str] | None:
    state_file = BASE / "state.json"
    if not state_file.exists():
        return ("warn", "state.json missing")
    age_h = (time.time() - state_file.stat().st_mtime) / 3600
    if age_h > 30:
        return ("warn", f"no processing activity for {age_h:.0f}h (state.json stale)")
    return None


def check_failures(prev_keys: list[str]) -> tuple[str, str] | None:
    try:
        failures = json.loads((BASE / "failures.json").read_text())
    except Exception:
        return None
    new = [k for k in failures if k not in set(prev_keys)]
    if new:
        sample = "; ".join(
            f"{k}: {failures[k].get('last_reason', '?')[:60]}" for k in new[:5]
        )
        return (
            "warn",
            f"{len(new)} newly failed msg(s) since last check: {sample}",
        )
    return None


def check_pipeline_heartbeat() -> tuple[str, str] | None:
    """The watcher worker writes .pipeline_heartbeat at every pipeline step.
    A non-idle step that hasn't advanced in >3h means the pipeline is stuck
    mid-archive (hung download/parse/ingest)."""
    hb_file = BASE / ".pipeline_heartbeat"
    try:
        hb = json.loads(hb_file.read_text())
        age_h = (time.time() - hb.get("ts", 0)) / 3600
    except Exception:
        return None
    step = hb.get("step", "?")
    if step == "idle" or age_h <= 3:
        return None
    return (
        "warn",
        f"pipeline stuck on step '{step}' (msg {hb.get('msg_id', '?')}, "
        f"{hb.get('detail', '')}) for {age_h:.1f}h",
    )


def check_db() -> tuple[str, str] | None:
    try:
        import sqlite3

        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10) as conn:
            conn.execute("SELECT COUNT(*) FROM credentials").fetchone()
        return None
    except Exception as exc:
        return ("crit", f"credentials DB unreachable: {exc}")


def run_checks(prev_keys: list[str]) -> tuple[list[tuple[str, str]], str]:
    issues: list[tuple[str, str]] = []
    issues += check_services()
    journal_issues, journal_text = check_journal()
    issues += journal_issues
    for chk in (
        check_disk(),
        check_state_staleness(),
        check_failures(prev_keys),
        check_pipeline_heartbeat(),
        check_db(),
    ):
        if chk:
            issues.append(chk)
    return issues, journal_text


# ── context gathering + Gemini diagnosis ────────────────────────────────


def gather_context(issues: list[tuple[str, str]], journal_text: str) -> str:
    df = run(["df", "-h", "/"])
    downloads = run(
        ["bash", "-lc", "du -sh /opt/tg-watcher/downloads 2>/dev/null; "
         "ls -lt /opt/tg-watcher/downloads | head -8"]
    )
    try:
        failures = (BASE / "failures.json").read_text()[:1500]
    except Exception:
        failures = "<unreadable>"
    try:
        hb = (BASE / ".pipeline_heartbeat").read_text()[:300]
    except Exception:
        hb = "<no heartbeat>"
    journal_tail = "\n".join(journal_text.splitlines()[-40:])
    issue_text = "\n".join(f"[{sev}] {msg}" for sev, msg in issues)
    return (
        f"HOST: {run(['hostname']).strip()}\nTIME: {now_utc()}\n\n"
        f"ISSUES:\n{issue_text}\n\n"
        f"DISK:\n{df}\n\nSERVICES:\n"
        f"{run(['systemctl', 'is-active', 'tg-watcher']).strip()} tg-watcher, "
        f"{run(['systemctl', 'is-active', 'cred-search']).strip()} cred-search\n\n"
        f"DOWNLOADS:\n{downloads}\n\n"
        f"FAILURES.JSON:\n{failures}\n\n"
        f"PIPELINE HEARTBEAT:\n{hb}\n\n"
        f"TG-WATCHER JOURNAL (last 40 lines):\n{journal_tail}\n"
    )


def gemini_diagnose(context: str) -> str:
    """Ask Gemini for a root-cause analysis. Returns '' on any failure."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return ""
    prompt = (
        "You are the on-call diagnostic agent for a VPS running a Telegram "
        "channel watcher (python/telethon, downloads credential-dump archives, "
        "extracts with 7z, parses to records.jsonl, ingests into SQLite, "
        "serves a Flask search UI). Analyze the health-check context below. "
        "Reply in under 150 words: 1) most likely root cause per issue, "
        "2) the single most urgent action, 3) exact fix command(s) if "
        "applicable. Be concrete, no hedging.\n\n"
    )
    for model in GEMINI_MODELS:
        try:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={key}"
            )
            body = json.dumps(
                {"contents": [{"parts": [{"text": prompt + context[:24000]}]}]}
            ).encode()
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read())
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return f"\n\n\u2728 Gemini diagnosis ({model}):\n{text.strip()[:2500]}"
        except Exception:
            continue
    return ""


# ── Telegram notify ─────────────────────────────────────────────────────


async def _send_telegram(text: str) -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not (SESSION_STRING and API_ID and API_HASH):
        raise RuntimeError("TG session/env not configured")
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TG session not authorized")
        # Telegram hard limit is 4096 chars/message
        for i in range(0, len(text), 3900):
            await client.send_message("me", text[i : i + 3900])
    finally:
        await client.disconnect()


def notify(text: str) -> bool:
    try:
        asyncio.run(_send_telegram(text))
        return True
    except Exception as exc:
        log_line(f"TELEGRAM NOTIFY FAILED: {exc}")
        return False


# ── main ────────────────────────────────────────────────────────────────


def main() -> None:
    force = "--force" in sys.argv
    status_only = "--status" in sys.argv

    st = load_health_state()
    prev_keys = st.get("failure_keys", [])
    issues, journal_text = run_checks(prev_keys)

    try:
        failures = json.loads((BASE / "failures.json").read_text())
        st["failure_keys"] = list(failures.keys())
    except Exception:
        st["failure_keys"] = prev_keys

    has_crit = any(sev == "crit" for sev, _ in issues)
    has_warn = any(sev == "warn" for sev, _ in issues)

    if status_only:
        print(f"tg-health @ {now_utc()}")
        if not issues:
            print("OK: all checks passed")
        for sev, msg in issues:
            print(f"[{sev.upper()}] {msg}")
        print(f"failures tracked: {len(st['failure_keys'])}")
        return

    if not issues:
        if force:
            log_line("forced test alert (healthy)")
            notify(
                "\u2705 tg-health test alert: monitoring is alive and all "
                f"checks pass. @ {now_utc()}"
            )
        if st.get("had_issues"):
            st["had_issues"] = False
            save_health_state(st)
            log_line("recovered - sending recovery notice")
            notify("\u2705 tg-watcher VPS: all issues cleared, back to normal.")
        else:
            log_line("ok - all checks passed")
        save_health_state(st)
        return

    st["had_issues"] = True
    limit_s = CRIT_RESEND_S if has_crit else WARN_RESEND_S
    bucket = "crit" if has_crit else "warn"
    last = st.get("last_alert", {}).get(bucket, 0)
    if not force and time.time() - last < limit_s:
        log_line(f"issues present ({bucket}) - rate limited, not re-sending")
        save_health_state(st)
        return

    st.setdefault("last_alert", {})[bucket] = time.time()
    save_health_state(st)

    context = gather_context(issues, journal_text)
    diagnosis = gemini_diagnose(context)

    header = "\U0001F6A8 tg-watcher VPS health alert" if has_crit else "\u26A0\uFE0F tg-watcher VPS health warning"
    body = f"{header} @ {now_utc()}\n\n" + "\n".join(
        f"[{sev.upper()}] {msg}" for sev, msg in issues
    )
    if diagnosis:
        body += diagnosis
    else:
        body += "\n\n(Gemini diagnosis unavailable - see /var/log/tg-health.log)"
    log_line(f"alert sent ({bucket}): " + "; ".join(m for _, m in issues))
    if not notify(body):
        # notify failed - reset the rate-limit stamp so the next run retries
        st["last_alert"][bucket] = 0
        save_health_state(st)


if __name__ == "__main__":
    main()
