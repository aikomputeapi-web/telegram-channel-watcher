"""Standalone visitor fingerprint collector — drop into any Flask site.

Self-contained: owns its own SQLite DB, schema, routes, admin viewer, and
client-side FingerprintJS loader. No dependency on the telegram-channel-watcher
credential DB or its schema.

Captures a FingerprintJS v4 visitorId + raw components from every visitor
(pre-login) and stores them in a separate SQLite DB. Mirrors Browser
Automation Studio's fingerprint-server capture model (passive collection,
not spoofing).

== Quick start ==

    from flask import Flask
    from visitor_fingerprints import init_fingerprint_collector

    app = Flask(__name__)
    app.secret_key = "..."  # required for the admin viewer session
    init_fingerprint_collector(app, admin_guard=lambda: current_user.is_admin)

    # that's it — POST /api/fingerprint collects, GET /admin/fingerprints views

== Env overrides ==

    FP_DB_PATH       path to fingerprint SQLite DB (default: fingerprints.db)
    FP_ADMIN_USER    admin username for viewer (default: admin)
    FP_ADMIN_PASS    admin password for viewer (no default; viewer is open if unset)

== Embedding in a page ==

    {{ fingerprint_script() | safe }}

    injects the FingerprintJS loader that POSTs to /api/fingerprint.

If not using the Jinja helper, just include this snippet on any page:

    <script>
    (async function(){try{const F=await import('https://openfpcdn.io/fingerprintjs/v4');
    const fp=await F.load();const r=await fp.get();
    fetch('/api/fingerprint',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({visitorId:r.visitorId,components:r.components,userAgent:navigator.userAgent})});
    }catch(e){console.warn('fingerprint error',e);}})();
    </script>
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

log = logging.getLogger("visitor_fingerprints")

DB_PATH = Path(os.getenv("FP_DB_PATH", "fingerprints.db"))
ADMIN_USER = os.getenv("FP_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("FP_ADMIN_PASS", "")
PER_PAGE = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS fingerprints (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    visitor_id    TEXT NOT NULL,
    user_agent    TEXT,
    ip_address    TEXT,
    components    TEXT,
    country       TEXT,
    region        TEXT,
    city          TEXT,
    latitude      REAL,
    longitude     REAL,
    timezone      TEXT,
    seen_at       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fp_visitor ON fingerprints(visitor_id);
CREATE INDEX IF NOT EXISTS idx_fp_seen    ON fingerprints(seen_at);
"""

# Columns added after the first release; migrated into existing DBs via ALTER.
GEO_COLUMNS = {
    "country": "TEXT",
    "region": "TEXT",
    "city": "TEXT",
    "latitude": "REAL",
    "longitude": "REAL",
    "timezone": "TEXT",
}

# ip -> geo dict cache (process lifetime). ip-api.com free tier caps at
# 45 req/min, so repeat visitors from the same IP must not re-hit the API.
_GEO_CACHE: dict[str, dict | None] = {}
_GEO_CACHE_MAX = 4096


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA mmap_size=268435456")


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """ALTER TABLE ADD COLUMN for any GEO_COLUMNS missing from an existing DB.
    CREATE TABLE IF NOT EXISTS can't evolve an existing table."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(fingerprints)")}
    for col, col_type in GEO_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE fingerprints ADD COLUMN {col} {col_type}")
    conn.commit()


def init_db(db_path: Path | None = None) -> None:
    """Create the fingerprints DB + table if missing. Idempotent."""
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        _apply_pragmas(conn)
        conn.executescript(SCHEMA)
        _migrate_columns(conn)
        conn.commit()
    log.info("Fingerprint DB initialized at %s", db_path)


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    return addr.is_global and not addr.is_multicast


def geo_lookup(ip: str | None) -> dict | None:
    """Best-effort IP geolocation via ip-api.com (free, no key, HTTP).
    Returns {country, region, city, latitude, longitude, timezone} or None.
    Cached per-IP; private/invalid IPs return None. Never raises."""
    ip = (ip or "").strip()
    if not ip or not _is_public_ip(ip):
        return None
    if ip in _GEO_CACHE:
        return _GEO_CACHE[ip]
    try:
        url = (
            "http://ip-api.com/json/" + urllib.request.quote(ip)
            + "?fields=status,country,regionName,city,lat,lon,timezone"
        )
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if not isinstance(data, dict) or data.get("status") != "success":
            raise ValueError(data.get("message", "lookup failed"))
        geo = {
            "country": data.get("country"),
            "region": data.get("regionName"),
            "city": data.get("city"),
            "latitude": data.get("lat"),
            "longitude": data.get("lon"),
            "timezone": data.get("timezone"),
        }
    except Exception as exc:
        log.warning("geo lookup failed for %s: %s", ip, exc)
        geo = None
    if len(_GEO_CACHE) >= _GEO_CACHE_MAX:
        _GEO_CACHE.clear()
    _GEO_CACHE[ip] = geo
    return geo


def store_fingerprint(
    visitor_id: str,
    components,
    user_agent: str,
    ip_address: str | None = None,
    geo: dict | None = None,
    db_path: Path | None = None,
) -> None:
    """Persist one FingerprintJS capture. JSON-encodes components so raw
    signals survive intact for admin review. Silent on failure — fingerprint
    collection must never break the visitor page load.

    NOTE: assumes the table already exists. Call init_db() once at app
    startup, or pass db_path pointing at an initialized DB."""
    db_path = db_path or DB_PATH
    init_db(db_path)  # cheap CREATE IF NOT EXISTS; safe to call each capture
    try:
        comp_json = json.dumps(components, default=str)
    except (TypeError, ValueError):
        comp_json = None
    g = geo or {}
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO fingerprints "
            "(visitor_id, user_agent, ip_address, components, "
            " country, region, city, latitude, longitude, timezone) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                visitor_id, user_agent, ip_address, comp_json,
                g.get("country"), g.get("region"), g.get("city"),
                g.get("latitude"), g.get("longitude"), g.get("timezone"),
            ),
        )
        conn.commit()


def backfill_geo(db_path: Path | None = None, limit: int = 1000) -> int:
    """Geo-lookup rows captured before geo columns existed. Returns count
    of rows updated. Respects ip-api.com rate limits via geo_lookup cache.
    Call once after deploying this schema change."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return 0
    init_db(db_path)  # migrate geo columns in if missing
    updated = 0
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, ip_address FROM fingerprints "
            "WHERE country IS NULL AND ip_address IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        for r in rows:
            geo = geo_lookup(r["ip_address"])
            if not geo:
                continue
            conn.execute(
                "UPDATE fingerprints SET country=?, region=?, city=?, "
                "latitude=?, longitude=?, timezone=? WHERE id=?",
                (
                    geo.get("country"), geo.get("region"), geo.get("city"),
                    geo.get("latitude"), geo.get("longitude"),
                    geo.get("timezone"), r["id"],
                ),
            )
            updated += 1
        conn.commit()
    log.info("Geo backfill: updated %d rows", updated)
    return updated


def list_fingerprints(
    db_path: Path | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (fingerprint_rows, total_count) for the admin view, newest first."""
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return [], 0
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
        rows = conn.execute(
            "SELECT id, visitor_id, user_agent, ip_address, components, "
            "country, region, city, latitude, longitude, timezone, seen_at "
            "FROM fingerprints ORDER BY seen_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # Show visit time both in UTC (as stored) and localized to the
        # visitor's geo timezone when known.
        d["seen_at_local"] = _localize_utc(d.get("seen_at"), d.get("timezone"))
        out.append(d)
    return out, total


def _localize_utc(seen_at: str | None, tz_name: str | None) -> str | None:
    """Convert a UTC 'YYYY-MM-DD HH:MM:SS' seen_at string to the given
    IANA timezone. Returns None on any failure."""
    if not seen_at or not tz_name:
        return None
    try:
        naive = datetime.strptime(str(seen_at)[:19], "%Y-%m-%d %H:%M:%S")
        local = naive.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


# ── client script ──────────────────────────────────────────────────────

FP_SCRIPT = """<script>
(async function(){try{const F=await import('https://openfpcdn.io/fingerprintjs/v4');
const fp=await F.load();const r=await fp.get();
fetch('/api/fingerprint',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({visitorId:r.visitorId,components:r.components,userAgent:navigator.userAgent})});
}catch(e){console.warn('fingerprint error',e);}})();
</script>"""


def fingerprint_script() -> str:
    """Jinja helper: emit the FingerprintJS loader that POSTs the visitor
    fingerprint to /api/fingerprint. Use as: {{ fingerprint_script() | safe }}"""
    return FP_SCRIPT


# ── admin viewer page ───────────────────────────────────────────────────

VIEWER_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Visitor Fingerprints</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; padding: 20px; }
h1 { color: #58a6ff; margin-bottom: 4px; font-size: 24px; }
.nav { margin-bottom: 16px; font-size: 13px; }
.nav a { color: #8b949e; text-decoration: none; }
.nav a:hover { color: #e0e0e0; }
.cards { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
        padding: 14px 18px; min-width: 140px; }
.card .num { color: #58a6ff; font-size: 22px; font-weight: 700; }
.card .lbl { color: #8b949e; font-size: 11px; text-transform: uppercase; }
table { width: 100%; border-collapse: collapse; background: #0d1117;
        border: 1px solid #30363d; border-radius: 6px; overflow: hidden; }
th { background: #161b22; color: #8b949e; text-align: left; padding: 10px 12px;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
td { padding: 10px 12px; border-top: 1px solid #21262d; font-size: 13px; }
tr:hover td { background: #161b22; }
.muted { color: #6e7681; font-size: 12px; }
.loc { color: #d2a8ff; font-size: 12px; }
.loc small { color: #6e7681; }
.vid { color: #7ee787; font-family: monospace; font-size: 12px; }
.ua { color: #8b949e; font-family: monospace; font-size: 11px; max-width: 320px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.det { cursor: pointer; color: #58a6ff; }
.det:hover { text-decoration: underline; }
.details pre { color: #c9d1d9; font-size: 11px; white-space: pre-wrap; word-break: break-all; }
.pagination { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
.pagination a, .pagination span { padding: 6px 12px; border-radius: 4px;
    text-decoration: none; font-size: 14px; }
.pagination a { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.pagination a:hover { background: #30363d; }
.pagination .current { background: #1f6feb; color: #fff; }
.empty { text-align:center; padding:40px; color:#8b949e; }
</style>
</head>
<body>
<h1>Visitor Fingerprints</h1>
<div class="cards">
  <div class="card"><div class="num">{{ total }}</div><div class="lbl">Captures</div></div>
  <div class="card"><div class="num">{{ unique }}</div><div class="lbl">Unique Visitors</div></div>
</div>
<table>
  <tr><th>Visit (UTC)</th><th>Local Time</th><th>Visitor ID</th><th>IP Address</th><th>Location</th><th>User Agent</th><th>Components</th></tr>
  {% for r in rows %}
  <tr>
    <td class="muted">{{ r.seen_at }}</td>
    <td class="muted">{{ r.seen_at_local or '—' }}</td>
    <td class="vid">{{ r.visitor_id }}</td>
    <td class="muted">{{ r.ip_address or '—' }}</td>
    <td class="loc">
      {% if r.city or r.region or r.country %}
        {{ [r.city, r.region] | select | join(', ') }}
        <small>{{ r.country }}</small>
        <br><small>{{ r.timezone or '' }}</small>
      {% else %}<span class="muted">—</span>{% endif %}
    </td>
    <td class="ua" title="{{ r.user_agent }}">{{ r.user_agent }}</td>
    <td>
      {% if r.components %}
      <span class="det" onclick="var d=this.parentNode.nextElementSibling; d.style.display=d.style.display=='block'?'none':'block';">show</span>
      {% else %}<span class="muted">—</span>{% endif %}
    </td>
  </tr>
  {% if r.components %}
  <tr class="details"><td colspan="7"><pre>{{ r.components }}</pre></td></tr>
  {% endif %}
  {% endfor %}
</table>
<div class="pagination">
  {% if page > 1 %}<a href="?page={{ page - 1 }}">Prev</a>{% endif %}
  <span class="current">Page {{ page }} of {{ total_pages }}</span>
  {% if page < total_pages %}<a href="?page={{ page + 1 }}">Next</a>{% endif %}
</div>
{% if not rows %}<div class="empty">No fingerprints captured yet.</div>{% endif %}
</body>
</html>"""


# ── collector endpoint ──────────────────────────────────────────────────


# ── collector endpoint ──────────────────────────────────────────────────────────────────────

def _client_ip(req) -> str:
    """Extract the real client IP from behind a Cloudflare tunnel / proxy.
    Priority: CF-Connecting-IP > first hop of X-Forwarded-For > remote_addr.
    X-Forwarded-For can be a chain 'client, proxy1, ...' — only the first
    entry is the visitor."""
    cf_ip = (req.headers.get("CF-Connecting-IP") or "").strip()
    if cf_ip:
        return cf_ip.split(",")[0].strip()
    xff = (req.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return req.remote_addr or ""


def init_fingerprint_collector(
    app: Flask,
    admin_guard=None,
    db_path: Path | None = None,
) -> None:
    """Register the fingerprint collector endpoints on a Flask app.

    Args:
        app: the Flask app to extend
        admin_guard: optional callable returning True if the current request
            is authorized to view /admin/fingerprints. If None, the viewer
            requires session['fp_admin'] == True (set by /fp/login).
        db_path: override the fingerprint DB path (defaults to FP_DB_PATH env
            or fingerprints.db)

    Wires:
        POST /api/fingerprint   — no auth, collects visitor fingerprint
        GET  /admin/fingerprints — authed viewer (guarded by admin_guard)
        GET  /fp/login, /fp/logout — built-in admin login if no external guard
    """
    db = db_path or DB_PATH
    if not isinstance(db, Path):
        db = Path(db)
    init_db(db)

    @app.route("/api/fingerprint", methods=["POST"])
    def _collect_fingerprint():
        data = request.get_json(silent=True) or {}
        visitor_id = (data.get("visitorId") or "").strip()
        if not visitor_id:
            return jsonify({"ok": False, "error": "missing visitorId"}), 400
        components = data.get("components") or {}
        user_agent = data.get("userAgent") or request.headers.get("User-Agent", "")
        ip_address = _client_ip(request)
        geo = geo_lookup(ip_address)  # best-effort; None on failure
        try:
            store_fingerprint(
                visitor_id=visitor_id,
                components=components,
                user_agent=user_agent,
                ip_address=ip_address,
                geo=geo,
                db_path=db,
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True})

    def _viewer_access_ok() -> bool:
        if admin_guard is not None:
            try:
                return bool(admin_guard())
            except Exception:
                return False
        return bool(session.get("fp_admin"))

    @app.route("/admin/fingerprints")
    def _admin_fingerprints():
        if not _viewer_access_ok():
            return redirect(url_for("_fp_login"))
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        per_page = PER_PAGE
        offset = (page - 1) * per_page
        rows, total = list_fingerprints(db, limit=per_page, offset=offset)
        unique = len({r["visitor_id"] for r in rows}) if rows else 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        return render_template_string(
            VIEWER_PAGE,
            rows=rows,
            total=total,
            unique=unique,
            page=page,
            total_pages=total_pages,
        )

    # built-in admin login — only registered if no external guard provided
    if admin_guard is None:

        @app.route("/fp/login", methods=["GET", "POST"])
        def _fp_login():
            msg = None
            if request.method == "POST":
                u = request.form.get("username", "").strip()
                p = request.form.get("password", "").strip()
                if ADMIN_PASS and u == ADMIN_USER and p == ADMIN_PASS:
                    session["fp_admin"] = True
                    return redirect(url_for("_admin_fingerprints"))
                msg = "Invalid credentials"
            return render_template_string(
                """<!DOCTYPE html><html><head><meta charset="UTF-8">
                <title>Fingerprint Admin</title>
                <style>body{font-family:sans-serif;background:#0f1117;color:#e0e0e0;
                display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
                .box{background:#161b22;border:1px solid #30363d;border-radius:12px;
                padding:32px;max-width:320px}h1{color:#58a6ff;font-size:20px;text-align:center}
                input{width:100%;padding:10px;margin:6px 0;background:#0f1117;border:1px solid #30363d;
                border-radius:6px;color:#e0e0e0;box-sizing:border-box}button{width:100%;padding:10px;
                background:#238636;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600}
                .msg{color:#f85149;text-align:center;font-size:13px;margin-top:8px}</style></head>
                <body><div class="box"><h1>Fingerprint Admin</h1>
                <form method="POST">
                <input name="username" placeholder="Username" required autofocus>
                <input name="password" type="password" placeholder="Password" required>
                <button type="submit">Sign in</button>
                </form>{% if msg %}<div class="msg">{{ msg }}</div>{% endif %}</div></body></html>""",
                msg=msg,
            )

        @app.route("/fp/logout")
        def _fp_logout():
            session.pop("fp_admin", None)
            return redirect(url_for("_fp_login"))


__all__ = [
    "DB_PATH",
    "SCHEMA",
    "GEO_COLUMNS",
    "init_db",
    "store_fingerprint",
    "backfill_geo",
    "geo_lookup",
    "list_fingerprints",
    "fingerprint_script",
    "init_fingerprint_collector",
]
