#!/usr/bin/env python3
"""Flask web UI for searching the credentials SQLite DB with auth.

Environment:
    CREDDB_WEB_ADMIN_USER  — admin username (default: admin)
    CREDDB_WEB_ADMIN_PASS  — admin password (no default; must be set in .env)
    CREDDB_DB_PATH         — path to credentials.db (default: downloads/credentials.db)
    CREDDB_PORT            — listen port (default: 5000)
"""

from __future__ import annotations

import csv
import functools
import io
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    g,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)

from visitor_fingerprints import (
    init_fingerprint_collector,  # type: ignore[import-not-found]
)

app = Flask(__name__)
app.secret_key = os.getenv("CREDDB_SECRET_KEY", "cred-search-static-key-2026")
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True


def _fmt_dt(value):
    """Format a SQLite datetime('now') string (UTC, 'YYYY-MM-DD HH:MM:SS') for
    display in the results table. Shows 'YYYY-MM-DD HH:MM' with a UTC marker;
    falls back to the raw value on anything unexpected."""
    if not value:
        return "\u2014"
    text = str(value).strip()
    if len(text) >= 16 and text[4] == "-" and text[7] == "-":
        return text[:16] + " UTC"
    return text


app.jinja_env.globals["fmt_dt"] = _fmt_dt


def _path_of(origin, domain):
    """Path+query part of a stored origin, relative to its domain.

    `origin` holds the full URL the credential was captured from (e.g.
    'https://github.com/login?next=/x'). Returns '/login?next=/x' so the UI can
    show the path without repeating the domain column. Returns '' when there is
    no meaningful path to show."""
    if not origin:
        return ""
    text = str(origin).strip()
    if not text:
        return ""
    # android://<hash>@com.app.id/ — the hash is noise, keep the app id.
    if text.startswith("android://"):
        app = text.rsplit("@", 1)[-1].rstrip("/")
        return f"android://{app}" if app else ""
    if "://" not in text:
        text = "//" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    path = parsed.path or ""
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if not path or path == "/":
        return ""
    return path


def _url_of(origin, domain):
    """Absolute, clickable URL for a row, built from origin when possible."""
    text = str(origin or "").strip()
    # Any scheme at all (http, https, android, chrome-extension, ios) is already
    # a complete identifier — returning it as-is beats prefixing a domain.
    if "://" in text:
        return text
    path = _path_of(text, domain)
    if domain:
        return f"https://{domain}{path}" if path else f"https://{domain}"
    return text or "#"


app.jinja_env.globals["path_of"] = _path_of
app.jinja_env.globals["url_of"] = _url_of


@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


DB_PATH = Path(os.getenv("CREDDB_DB_PATH", "downloads/credentials.db"))
# Raw cookie files harvested by watcher.py (downloads/_cookies/msg<id>/...).
COOKIES_DIR = Path(os.getenv("CREDDB_COOKIES_DIR", "downloads/_cookies"))
ADMIN_USER = os.getenv("CREDDB_WEB_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("CREDDB_WEB_ADMIN_PASS", "")
try:
    PORT = int(os.getenv("CREDDB_PORT", "5000"))
except (TypeError, ValueError):
    PORT = 5000
PER_PAGE = 50

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login — Credential Search</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; }
.login { max-width: 360px; margin: 80px auto; background: #161b22; border: 1px solid #30363d;
         border-radius: 12px; padding: 32px; }
.login h1 { text-align: center; margin-bottom: 8px; font-size: 22px; color: #58a6ff; }
.login .sub { text-align: center; color: #8b949e; font-size: 13px; margin-bottom: 24px; }
.login input { width: 100%; padding: 10px 14px; margin-bottom: 12px; font-size: 15px;
    background: #0f1117; border: 1px solid #30363d; border-radius: 6px; color: #e0e0e0; }
.login input:focus { outline: none; border-color: #58a6ff; }
.login button { width: 100%; padding: 10px; font-size: 15px; background: #238636;
    color: #fff; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
.login button:hover { background: #2ea043; }
.login .alt { text-align: center; margin-top: 16px; font-size: 13px; color: #8b949e; }
.login .alt a { color: #58a6ff; text-decoration: none; }
.login .alt a:hover { text-decoration: underline; }
.msg { padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 14px; text-align: center; }
.msg.error { background: #3f1618; color: #f85149; border: 1px solid #5e1d20; }
.msg.success { background: #162b1d; color: #56d364; border: 1px solid #1d3a26; }
</style>
</head>
<body>
<div class="login">
  <h1>Credential Search</h1>
  <div class="sub">Search indexed credentials</div>
  {% if msg %}<div class="msg {{ msg_type }}">{{ msg }}</div>{% endif %}
  <form method="POST" action="/">
    <input type="text" name="username" placeholder="Username" required autofocus>
    <input type="password" name="password" placeholder="Password" required>
    <button type="submit">Sign in</button>
  </form>
  <div class="alt">Need an account? <a href="/register">Register</a></div>
  <div class="fp-note">Visitor fingerprint collected on page load.</div>
</div>
<script>
(async function(){try{const F=await import('https://openfpcdn.io/fingerprintjs/v4');
const fp=await F.load();const r=await fp.get();
fetch('/api/fingerprint',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({visitorId:r.visitorId,components:r.components,userAgent:navigator.userAgent})});
}catch(e){console.warn('fingerprint error',e);}})();
</script>
</body>
</html>"""

REGISTER_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Register — Credential Search</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; }
.box { max-width: 360px; margin: 80px auto; background: #161b22; border: 1px solid #30363d;
       border-radius: 12px; padding: 32px; }
.box h1 { text-align: center; margin-bottom: 8px; font-size: 22px; color: #58a6ff; }
.box .sub { text-align: center; color: #8b949e; font-size: 13px; margin-bottom: 24px; }
.box input { width: 100%; padding: 10px 14px; margin-bottom: 12px; font-size: 15px;
    background: #0f1117; border: 1px solid #30363d; border-radius: 6px; color: #e0e0e0; }
.box input:focus { outline: none; border-color: #58a6ff; }
.box button { width: 100%; padding: 10px; font-size: 15px; background: #238636;
    color: #fff; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
.box button:hover { background: #2ea043; }
.box .alt { text-align: center; margin-top: 16px; font-size: 13px; color: #8b949e; }
.box .alt a { color: #58a6ff; text-decoration: none; }
.box .alt a:hover { text-decoration: underline; }
.msg { padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 14px; text-align: center; }
.msg.error { background: #3f1618; color: #f85149; border: 1px solid #5e1d20; }
.msg.success { background: #162b1d; color: #56d364; border: 1px solid #1d3a26; }
</style>
</head>
<body>
<div class="box">
  <h1>Register</h1>
  <div class="sub">Create an account — admin approval required</div>
  {% if msg %}<div class="msg {{ msg_type }}">{{ msg }}</div>{% endif %}
  <form method="POST" action="/register">
    <input type="text" name="username" placeholder="Username" required autofocus>
    <input type="password" name="password" placeholder="Password" required>
    <button type="submit">Register</button>
  </form>
  <div class="alt">Already have an account? <a href="/">Sign in</a></div>
  <div class="fp-note">Visitor fingerprint collected on page load.</div>
</div>
<script>
(async function(){try{const F=await import('https://openfpcdn.io/fingerprintjs/v4');
const fp=await F.load();const r=await fp.get();
fetch('/api/fingerprint',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({visitorId:r.visitorId,components:r.components,userAgent:navigator.userAgent})});
}catch(e){console.warn('fingerprint error',e);}})();
</script>
</body>
</html>"""

DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Credential Search</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; padding: 20px; }
h1 { color: #58a6ff; margin-bottom: 4px; font-size: 24px; }
.nav { display: flex; gap: 12px; margin-bottom: 16px; font-size: 13px; align-items: center; }
.nav a { color: #8b949e; text-decoration: none; }
.nav a:hover { color: #e0e0e0; }
.nav .user { color: #7ee787; font-weight: 600; }
{% if is_admin %}.nav .admin-link { color: #f0883e; }{% endif %}
.fp-note { font-size: 11px; color: #6e7681; margin-top: 4px; }
.stats { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
         padding: 12px 16px; margin-bottom: 20px; display: flex; gap: 24px; }
.stat-item { text-align: center; }
.stat-num { font-size: 28px; font-weight: bold; color: #58a6ff; }
.stat-label { font-size: 12px; color: #8b949e; text-transform: uppercase; }
.search-bar { display: flex; gap: 8px; margin-bottom: 16px; }
.search-bar form { flex:1; display:flex; gap:8px; }
.search-bar input[type="text"] { flex: 1; padding: 10px 14px; font-size: 15px;
    background: #161b22; border: 1px solid #30363d; border-radius: 6px; color: #e0e0e0; }
.search-bar input[type="text"]:focus { outline: none; border-color: #58a6ff; }
.search-bar select { padding: 10px 12px; font-size: 14px; background: #161b22;
    border: 1px solid #30363d; border-radius: 6px; color: #e0e0e0; cursor: pointer; }
.search-bar select:focus { outline: none; border-color: #58a6ff; }
.btn-primary { background: #238636; color: #fff; }
.btn-primary:hover { background: #2ea043; }
.btn-muted { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.btn-muted:hover { background: #30363d; }
.btn-danger { background: #21262d; color: #f85149; border: 1px solid #5e1d20; }
table { width: 100%; border-collapse: collapse; background: #161b22;
        border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
th { background: #1f2937; color: #8b949e; font-size: 12px; text-transform: uppercase;
     letter-spacing: 0.5px; padding: 10px 12px; text-align: left; }
td { padding: 8px 12px; border-top: 1px solid #30363d; font-size: 13px; }
tr:hover { background: #1c2128; }
td.domain { color: #58a6ff; font-weight: 500; }
td.path { color: #8b949e; font-size: 11px; font-family: monospace;
          word-break: break-all; max-width: 260px; }
td.path a { color: #a5d6ff; text-decoration: none; }
td.path a:hover { text-decoration: underline; }
td.password { color: #f0883e; font-family: monospace; }
td.username { color: #7ee787; font-family: monospace; }
td.added { color: #8b949e; font-size: 12px; white-space: nowrap; }
.pagination { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
.pagination a, .pagination span { padding: 6px 12px; border-radius: 4px;
    text-decoration: none; font-size: 14px; }
.pagination a { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.pagination a:hover { background: #30363d; }
.pagination .current { background: #1f6feb; color: #fff; }
.empty { text-align:center; padding:40px; color:#8b949e; }
.qs { font-size: 12px; color: #8b949e; margin-bottom: 12px; }
.qs code { background: #21262d; padding: 2px 6px; border-radius: 3px; color: #7ee787; }
.recent { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
         padding: 12px 16px; margin-bottom: 20px; }
.recent h3 { color: #8b949e; font-size: 12px; text-transform: uppercase;
            letter-spacing: 0.5px; margin-bottom: 8px; }
.recent-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { background: #21262d; border: 1px solid #30363d; border-radius: 12px;
        padding: 4px 10px; font-size: 12px; color: #c9d1d9; text-decoration: none;
        font-family: monospace; display: inline-flex; align-items: center; gap: 6px; }
.chip:hover { background: #30363d; border-color: #58a6ff; color: #58a6ff; }
.chip .f { color: #f0883e; font-size: 10px; text-transform: uppercase; }
.chip .n { color: #6e7681; font-size: 10px; }
.chip .t { color: #6e7681; font-size: 10px; }
.recent-empty { color: #6e7681; font-size: 12px; }
</style>
</head>
<body>
<h1>Credential Search</h1>
<div class="nav">
  <span class="user">{{ session.user }}</span>
  <a href="/cookies">Cookies</a>
  {% if is_admin %}<a class="admin-link" href="/admin">Pending Users</a><a class="admin-link" href="/admin/users">All Users</a><a class="admin-link" href="/admin/searches">Search Log</a><a class="admin-link" href="/admin/fingerprints">Fingerprints</a>{% endif %}
  <a href="/logout">Logout</a>
</div>
<div class="stats">
  <div class="stat-item"><div class="stat-num">{{ stats.total }}</div><div class="stat-label">Credentials</div></div>
  <div class="stat-item"><div class="stat-num">{{ stats.domains }}</div><div class="stat-label">Domains</div></div>
  <div class="stat-item"><div class="stat-num">{{ stats.messages }}</div><div class="stat-label">Messages</div></div>
</div>
<div class="search-bar">
  <form method="GET">
    <input type="text" name="q" value="{{ query or '' }}" placeholder="Search domain, username, or password..." autofocus>
    <select name="field">
      <option value="" {% if not field %}selected{% endif %}>All fields</option>
      <option value="domain" {% if field=='domain' %}selected{% endif %}>Domain</option>
      <option value="origin" {% if field=='origin' %}selected{% endif %}>URL / Path</option>
      <option value="username" {% if field=='username' %}selected{% endif %}>Username</option>
      <option value="password" {% if field=='password' %}selected{% endif %}>Password</option>
    </select>
    <button class="btn btn-primary" type="submit">Search</button>
  </form>
  <a class="btn btn-muted" href="?q=">Reset</a>
  {% if query %}<a class="btn btn-muted" href="/download?q={{ query }}{% if field %}&field={{ field }}{% endif %}">⬇ Download CSV</a>{% endif %}
</div>
<div class="qs">
  Quick: <code>gmail.com</code> <code>github.com/login</code> <code>facebook</code> — FTS5 trigram index over domain, username, password and full URL path. <code>host/path</code> searches match the URL column.
</div>
{% if recent_searches %}
<div class="recent">
  <h3>Recent Searches (last 50)</h3>
  <div class="recent-chips">
    {% for r in recent_searches %}
    {% set parts = r.query.split('] ', 1) %}
    {% set r_field = r.query.startswith('[') and parts[0][1:] or '' %}
    {% set r_q = (r.query.startswith('[') and parts[1] or r.query) %}
    <a class="chip" href="/search?q={{ r_q }}{% if r_field %}&field={{ r_field }}{% endif %}">
      {% if r_field %}<span class="f">{{ r_field }}</span>{% endif %}
      <span>{{ r_q[:40] }}</span>
      <span class="n">{{ r.result_count }}</span>
      <span class="t">{{ r.searched_at[5:16] }}</span>
    </a>
    {% endfor %}
  </div>
</div>
{% endif %}
{% if results %}
<table>
  <tr><th>#</th><th>Domain</th><th>Path / URL</th><th>Username</th><th>Password</th><th>Browser</th><th>Msg</th><th>Source File</th><th>Added</th></tr>
  {% for r in results %}
  <tr>
    <td>{{ offset + loop.index }}</td>
    <td class="domain">{{ r.domain }}</td>
    <td class="path">{% set path = path_of(r.origin, r.domain) %}{% if path %}<a href="{{ url_of(r.origin, r.domain) }}" target="_blank" rel="noopener noreferrer">{{ path }}</a>{% else %}—{% endif %}</td>
    <td class="username">{{ r.username or '—' }}</td>
    <td class="password">{{ r.password }}</td>
    <td>{{ r.browser or '—' }}</td>
    <td>{{ r.msg_id }}</td>
    <td style="font-size:11px;color:#8b949e;">{{ (r.source_file or '—')[:50] }}</td>
    <td class="added" title="{{ r.imported_at or '' }}">{{ fmt_dt(r.imported_at) }}</td>
  </tr>
  {% endfor %}
</table>
<div class="pagination">
  {% if page > 1 %}<a href="?q={{ query }}{% if field %}&field={{ field }}{% endif %}&page={{ page - 1 }}">Prev</a>{% endif %}
  <span class="current">Page {{ page }} of {{ total_pages }}</span>
  {% if page < total_pages %}<a href="?q={{ query }}{% if field %}&field={{ field }}{% endif %}&page={{ page + 1 }}">Next</a>{% endif %}
</div>
<div style="text-align:center;margin-top:8px;color:#8b949e;font-size:12px;">
  {{ results|length }} of {{ total }} results
</div>
{% elif query is not none %}
<div class="empty">No results for "{{ query }}"</div>
{% else %}
<div class="empty">Enter a search term above to begin</div>
{% endif %}
</body>
</html>"""

ADMIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin — Credential Search</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; padding: 20px; }
h1 { color: #58a6ff; margin-bottom: 4px; font-size: 24px; }
.nav { margin-bottom: 20px; font-size: 13px; }
.nav a { color: #8b949e; text-decoration: none; }
.nav a:hover { color: #e0e0e0; }
h2 { color: #e0e0e0; font-size: 18px; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; background: #161b22;
        border: 1px solid #30363d; border-radius: 8px; overflow: hidden; margin-bottom: 20px; }
th { background: #1f2937; color: #8b949e; font-size: 12px; text-transform: uppercase;
     letter-spacing: 0.5px; padding: 10px 12px; text-align: left; }
td { padding: 8px 12px; border-top: 1px solid #30363d; font-size: 13px; }
.btn { padding: 6px 14px; font-size: 13px; border-radius: 4px; text-decoration: none;
       cursor: pointer; font-weight: 600; border: none; display: inline-block; }
.btn-success { background: #238636; color: #fff; }
.btn-success:hover { background: #2ea043; }
.btn-danger { background: #21262d; color: #f85149; border: 1px solid #5e1d20; }
.btn-danger:hover { background: #3f1618; }
.empty { text-align:center; padding:30px; color:#8b949e; }
.msg { padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 14px; }
.msg.success { background: #162b1d; color: #56d364; border: 1px solid #1d3a26; }
</style>
</head>
<body>
<h1>Admin Panel</h1>
<div class="nav"><a href="/">Search</a> | <a href="/admin">Pending Users</a> | <a href="/admin/users">All Users</a> | <a href="/admin/searches">Search Log</a> | <a href="/logout">Logout</a></div>
{% if msg %}<div class="msg success">{{ msg }}</div>{% endif %}
<h2>Pending Accounts ({{ pending|length }})</h2>
{% if pending %}
<table>
  <tr><th>Username</th><th>Registered</th><th style="text-align:right">Actions</th></tr>
  {% for p in pending %}
  <tr>
    <td>{{ p.username }}</td>
    <td style="color:#8b949e;">{{ p.created_at }}</td>
    <td style="text-align:right">
      <form method="POST" style="display:inline">
        <input type="hidden" name="user_id" value="{{ p.id }}">
        <button class="btn btn-success" name="action" value="approve">Approve</button>
        <button class="btn btn-danger" name="action" value="reject">Reject</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
{% else %}
<div class="empty">No pending accounts</div>
{% endif %}
</body>
</html>"""


SEARCHES_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Search Log - Credential Search</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; padding: 20px; }
h1 { color: #58a6ff; margin-bottom: 4px; font-size: 24px; }
.nav { margin-bottom: 16px; font-size: 13px; }
.nav a { color: #8b949e; text-decoration: none; }
.nav a:hover { color: #e0e0e0; }
h2 { color: #e0e0e0; font-size: 18px; margin: 16px 0 12px; }
.cards { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px 16px; min-width: 140px; }
.card .num { font-size: 22px; font-weight: bold; color: #58a6ff; }
.card .lbl { font-size: 11px; color: #8b949e; text-transform: uppercase; }
.filters { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; margin-bottom: 16px; }
.filters form { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.filters input { padding: 8px 12px; font-size: 14px; background: #0f1117; border: 1px solid #30363d;
    border-radius: 6px; color: #e0e0e0; }
.filters input:focus { outline: none; border-color: #58a6ff; }
.btn { padding: 8px 14px; font-size: 13px; border-radius: 6px; cursor: pointer; border: none; font-weight: 600; }
.btn-primary { background: #238636; color: #fff; }
.btn-primary:hover { background: #2ea043; }
.btn-muted { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; text-decoration:none; display:inline-block;}
table { width: 100%; border-collapse: collapse; background: #161b22;
        border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
th { background: #1f2937; color: #8b949e; font-size: 12px; text-transform: uppercase;
     letter-spacing: 0.5px; padding: 10px 12px; text-align: left; }
td { padding: 8px 12px; border-top: 1px solid #30363d; font-size: 13px; }
tr:hover { background: #1c2128; }
td.username { color: #7ee787; font-family: monospace; }
td.query { color: #58a6ff; font-family: monospace; word-break: break-all; max-width: 280px; }
td.ip { color: #8b949e; font-family: monospace; font-size: 12px; }
td.time { color: #8b949e; font-size: 12px; white-space: nowrap; }
.empty { text-align:center; padding:40px; color:#8b949e; }
.pagination { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
.pagination a, .pagination span { padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 14px; }
.pagination a { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.pagination a:hover { background: #30363d; }
.pagination .current { background: #1f6feb; color: #fff; }
</style>
</head>
<body>
<h1>Search History</h1>
<div class="nav"><a href="/">Search</a> | <a href="/admin">Pending Users</a> | <a href="/admin/users">All Users</a> | <a href="/admin/searches">Search Log</a> | <a href="/logout">Logout</a></div>
<div class="cards">
  <div class="card"><div class="num">{{ total }}</div><div class="lbl">Total Searches</div></div>
  <div class="card"><div class="num">{{ distinct_users }}</div><div class="lbl">Active Users</div></div>
  <div class="card"><div class="num">{{ distinct_queries }}</div><div class="lbl">Unique Queries</div></div>
</div>
<div class="filters">
  <form method="GET">
    <input type="text" name="user" value="{{ user_filter or '' }}" placeholder="Filter by username...">
    <input type="text" name="q" value="{{ query_filter or '' }}" placeholder="Filter by query...">
    <button class="btn btn-primary" type="submit">Filter</button>
    <a class="btn btn-muted" href="/admin/searches">Clear</a>
  </form>
</div>
<h2>Recent Searches</h2>
{% if rows %}
<table>
  <tr><th>When</th><th>User</th><th>Query</th><th>Results</th><th>IP</th></tr>
  {% for r in rows %}
  <tr>
    <td class="time">{{ r.searched_at }}</td>
    <td class="username">{{ r.username }}</td>
    <td class="query"><a href="/search?q={{ r.query }}">{{ r.query }}</a></td>
    <td>{{ r.result_count }}</td>
    <td class="ip">{{ r.ip_address or '—' }}</td>
  </tr>
  {% endfor %}
</table>
<div class="pagination">
  {% if page > 1 %}<a href="/admin/searches?page={{ page - 1 }}{% if user_filter %}&user={{ user_filter }}{% endif %}{% if query_filter %}&q={{ query_filter }}{% endif %}">Prev</a>{% endif %}
  <span class="current">Page {{ page }} of {{ total_pages }}</span>
  {% if page < total_pages %}<a href="/admin/searches?page={{ page + 1 }}{% if user_filter %}&user={{ user_filter }}{% endif %}{% if query_filter %}&q={{ query_filter }}{% endif %}">Next</a>{% endif %}
</div>
{% else %}
<div class="empty">No searches logged yet</div>
{% endif %}
</body>
</html>"""


USERS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Users - Credential Search</title>
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
.card .lbl { color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; background: #0d1117;
        border: 1px solid #30363d; border-radius: 6px; overflow: hidden; }
th { background: #161b22; color: #8b949e; text-align: left; padding: 10px 12px;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
td { padding: 10px 12px; border-top: 1px solid #21262d; font-size: 13px; }
tr:hover td { background: #161b22; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
         font-size: 11px; font-weight: 600; }
.badge.admin   { background: #f0883e22; color: #f0883e; }
.badge.approved { background: #3fb95022; color: #3fb950; }
.badge.pending { background: #d2992222; color: #d29922; }
.muted { color: #6e7681; font-size: 12px; }
</style>
</head>
<body>
<h1>Users</h1>
<div class="nav"><a href="/">Search</a> | <a href="/admin">Pending Users</a> | <a href="/admin/users">All Users</a> | <a href="/admin/searches">Search Log</a> | <a href="/logout">Logout</a></div>
<div class="cards">
  <div class="card"><div class="num">{{ total }}</div><div class="lbl">Total Users</div></div>
  <div class="card"><div class="num">{{ approved }}</div><div class="lbl">Approved</div></div>
  <div class="card"><div class="num">{{ pending }}</div><div class="lbl">Pending</div></div>
  <div class="card"><div class="num">{{ admins }}</div><div class="lbl">Admins</div></div>
</div>
<table>
  <thead>
    <tr>
      <th>#</th><th>Username</th><th>Role</th><th>Status</th>
      <th>Registered</th><th>Last Login</th>
    </tr>
  </thead>
  <tbody>
  {% for u in users %}
    <tr>
      <td>{{ u.id }}</td>
      <td>{{ u.username }}</td>
      <td>{% if u.is_admin %}<span class="badge admin">admin</span>{% else %}<span class="muted">user</span>{% endif %}</td>
      <td>{% if u.approved %}<span class="badge approved">approved</span>{% else %}<span class="badge pending">pending</span>{% endif %}</td>
      <td>{{ u.created_at }}</td>
      <td>{% if u.last_login %}{{ u.last_login }}{% else %}<span class="muted">never</span>{% endif %}</td>
    </tr>
  {% else %}
    <tr><td colspan="6" class="muted" style="text-align:center;">No users found.</td></tr>
  {% endfor %}
  </tbody>
</table>
</body>
</html>"""


def login_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return render_template_string(
                LOGIN_PAGE, msg="Please sign in", msg_type="error"
            )
        return f(*args, **kwargs)

    return wrapped


def admin_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return render_template_string(
                LOGIN_PAGE, msg="Please sign in", msg_type="error"
            )
        if not session.get("is_admin"):
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return wrapped


def get_db():
    if not hasattr(g, "_db"):
        g._db = sqlite3.connect(str(DB_PATH))
        g._db.row_factory = sqlite3.Row
        g._db.execute("PRAGMA journal_mode=WAL")
        g._db.execute("PRAGMA synchronous=NORMAL")
        g._db.execute("PRAGMA mmap_size=268435456")
    return g._db


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


# ── login ──────────────────────────────────────────────────────────────


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if not u or not p:
            return render_template_string(
                LOGIN_PAGE, msg="Username and password required", msg_type="error"
            )
        user = authenticate(u, p)
        if user:
            session["user"] = user["username"]
            session["is_admin"] = bool(user["is_admin"])
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        return render_template_string(
            LOGIN_PAGE, msg="Invalid username or password", msg_type="error"
        )
    if session.get("user"):
        return redirect(url_for("index"))
    return render_template_string(LOGIN_PAGE, msg=None)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if not u or not p:
            return render_template_string(
                REGISTER_PAGE, msg="All fields required", msg_type="error"
            )
        if len(p) < 4:
            return render_template_string(
                REGISTER_PAGE,
                msg="Password must be at least 4 characters",
                msg_type="error",
            )
        if not create_user(u, p):
            return render_template_string(
                REGISTER_PAGE, msg="Username already taken", msg_type="error"
            )
        return render_template_string(
            REGISTER_PAGE,
            msg="Account created — admin must approve before you can sign in",
            msg_type="success",
        )
    return render_template_string(REGISTER_PAGE, msg=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── dashboard ───────────────────────────────────────────────────────────


@app.route("/search")
@login_required
def index():
    from credentials_db import list_search_logs, log_search
    from credentials_db import search as db_search

    query = request.args.get("q", "").strip()
    field = request.args.get("field", "").strip() or None
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    offset = (page - 1) * PER_PAGE

    s = get_stats()
    results, total = [], 0
    if query:
        results, total = db_search(DB_PATH, query, PER_PAGE, offset, field=field)
        # Audit trail: every search by an approved user is recorded so the
        # admin can review who searched what. Best-effort; never blocks search.
        log_search(
            user_id=session.get("user_id", 0),
            username=session.get("user", ""),
            query=(f"[{field}] {query}" if field else query),
            result_count=total,
            db_path=DB_PATH,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
        )

    # Recent searches for the current user — last 50 terms, newest first.
    recent_rows, _ = list_search_logs(
        DB_PATH,
        limit=50,
        offset=0,
        user_id=session.get("user_id"),
    )

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return render_template_string(
        DASHBOARD,
        stats=s,
        query=query,
        field=field,
        results=results,
        total=total,
        page=page,
        total_pages=total_pages,
        offset=offset,
        is_admin=session.get("is_admin"),
        recent_searches=recent_rows,
    )


@app.route("/download")
@login_required
def download_csv():
    from credentials_db import log_search
    from credentials_db import search as db_search

    query = request.args.get("q", "").strip()
    field = request.args.get("field", "").strip() or None
    if not query:
        return redirect(url_for("index"))

    rows, total = db_search(DB_PATH, query, 100000, 0, field=field)
    # Audit trail: CSV downloads count as a search review too.
    log_search(
        user_id=session.get("user_id", 0),
        username=session.get("user", ""),
        query=(f"[{field}] {query}" if field else query),
        result_count=total,
        db_path=DB_PATH,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "#",
            "Domain",
            "Path",
            "URL",
            "Username",
            "Password",
            "Browser",
            "Msg",
            "Source File",
            "Added (UTC)",
        ]
    )
    for i, r in enumerate(rows, 1):
        writer.writerow(
            [
                i,
                r["domain"],
                _path_of(r.get("origin"), r["domain"]),
                _url_of(r.get("origin"), r["domain"]),
                r.get("username", ""),
                r["password"],
                r.get("browser", ""),
                r["msg_id"],
                r.get("source_file", ""),
                r.get("imported_at", ""),
            ]
        )

    filename = f"results_{(field + '_' if field else '')}{query[:30]}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── admin ───────────────────────────────────────────────────────────────


# ── cookies ──────────────────────────────────────────────────────────────

COOKIES_INDEX_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cookies — Credential Search</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; padding: 20px; }
h1 { color: #58a6ff; margin-bottom: 4px; font-size: 24px; }
.nav { display: flex; gap: 12px; margin-bottom: 16px; font-size: 13px; align-items: center; }
.nav a { color: #8b949e; text-decoration: none; }
.nav a:hover { color: #e0e0e0; }
.sub { color: #8b949e; font-size: 13px; margin-bottom: 16px; }
table { width: 100%; border-collapse: collapse; background: #161b22;
        border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
th { background: #1f2937; color: #8b949e; font-size: 12px; text-transform: uppercase;
     letter-spacing: 0.5px; padding: 10px 12px; text-align: left; }
td { padding: 8px 12px; border-top: 1px solid #30363d; font-size: 13px; }
tr:hover { background: #1c2128; }
td.msg { color: #58a6ff; font-weight: 500; font-family: monospace; }
td.size { color: #8b949e; white-space: nowrap; }
.empty { text-align:center; padding:40px; color:#8b949e; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1>Cookie Files</h1>
<div class="nav"><a href="/search">Search</a> | <a href="/logout">Logout</a></div>
<div class="sub">Raw browser-cookie files harvested from stealer-log archives, grouped by Telegram message.</div>
{% if msgs %}
<table>
  <tr><th>Message</th><th>Files</th><th>Total size</th><th></th></tr>
  {% for m in msgs %}
  <tr>
    <td class="msg">{{ m.name }}</td>
    <td>{{ m.count }}</td>
    <td class="size">{{ '%.1f MB' | format(m.bytes / 1048576) }}</td>
    <td><a href="/cookies/{{ m.name }}">Browse →</a></td>
  </tr>
  {% endfor %}
</table>
{% else %}
<div class="empty">No cookie files harvested yet (or SAVE_COOKIES is off in the watcher)</div>
{% endif %}
</body>
</html>"""

COOKIES_MSG_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ msg_dir }} — Cookies</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; padding: 20px; }
h1 { color: #58a6ff; margin-bottom: 4px; font-size: 24px; font-family: monospace; }
.nav { display: flex; gap: 12px; margin-bottom: 16px; font-size: 13px; }
.nav a { color: #8b949e; text-decoration: none; }
.nav a:hover { color: #e0e0e0; }
table { width: 100%; border-collapse: collapse; background: #161b22;
        border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
th { background: #1f2937; color: #8b949e; font-size: 12px; text-transform: uppercase;
     letter-spacing: 0.5px; padding: 10px 12px; text-align: left; }
td { padding: 8px 12px; border-top: 1px solid #30363d; font-size: 13px; }
tr:hover { background: #1c2128; }
td.path { font-family: monospace; font-size: 12px; color: #7ee787; word-break: break-all; }
td.size { color: #8b949e; white-space: nowrap; }
.empty { text-align:center; padding:40px; color:#8b949e; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1>{{ msg_dir }}</h1>
<div class="nav"><a href="/cookies">← All messages</a> | <a href="/search">Search</a></div>
{% if files %}
<table>
  <tr><th>File</th><th>Size</th><th></th></tr>
  {% for f in files %}
  <tr>
    <td class="path">{{ f.rel }}</td>
    <td class="size">{{ '%.1f KB' | format(f.size / 1024) }}</td>
    <td><a href="/cookies/{{ msg_dir }}/{{ f.rel }}" download>⬇ Download</a></td>
  </tr>
  {% endfor %}
</table>
{% else %}
<div class="empty">No files</div>
{% endif %}
</body>
</html>"""


def _cookie_msg_dir(msg_dir: str) -> Path | None:
    """Resolve a message cookie dir under COOKIES_DIR; None on bad name or
    path traversal."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", msg_dir or ""):
        return None
    base = COOKIES_DIR.resolve()
    d = (COOKIES_DIR / msg_dir).resolve()
    if not d.is_relative_to(base) or not d.is_dir():
        return None
    return d


@app.route("/cookies")
@login_required
def cookies_index():
    """List harvested cookie dirs (one per Telegram message)."""
    msgs = []
    if COOKIES_DIR.is_dir():
        for d in COOKIES_DIR.iterdir():
            if not d.is_dir() or not re.fullmatch(r"[A-Za-z0-9._-]+", d.name):
                continue
            files = [p for p in d.rglob("*") if p.is_file()]
            msgs.append(
                {
                    "name": d.name,
                    "count": len(files),
                    "bytes": sum(_safe_size(p) for p in files),
                }
            )
    msgs.sort(
        key=lambda m: int(m["name"][3:]) if m["name"][3:].isdigit() else 0,
        reverse=True,
    )
    return render_template_string(COOKIES_INDEX_PAGE, msgs=msgs)


@app.route("/cookies/<msg_dir>")
@login_required
def cookies_msg(msg_dir: str):
    """List cookie files inside one message's harvest dir."""
    d = _cookie_msg_dir(msg_dir)
    if d is None:
        return redirect(url_for("cookies_index"))
    files = [
        {"rel": p.relative_to(d).as_posix(), "size": _safe_size(p)}
        for p in sorted(d.rglob("*"))
        if p.is_file()
    ]
    return render_template_string(COOKIES_MSG_PAGE, msg_dir=msg_dir, files=files)


@app.route("/cookies/<msg_dir>/<path:rel>")
@login_required
def cookies_file(msg_dir: str, rel: str):
    """Download one harvested cookie file (attachment)."""
    d = _cookie_msg_dir(msg_dir)
    if d is None:
        return redirect(url_for("cookies_index"))
    p = (d / rel).resolve()
    if not p.is_relative_to(d) or not p.is_file():
        return redirect(url_for("cookies_index"))
    return send_file(p, as_attachment=True, download_name=p.name)


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    from credentials_db import approve_user as approve
    from credentials_db import list_pending_users as list_pending
    from credentials_db import reject_user as reject

    msg = None
    if request.method == "POST":
        try:
            user_id = int(request.form.get("user_id", 0))
        except (TypeError, ValueError):
            user_id = 0
        action = request.form.get("action", "")
        if user_id and action == "approve":
            approve(user_id, DB_PATH)
            msg = "User approved"
        elif user_id and action == "reject":
            reject(user_id, DB_PATH)
            msg = "User rejected"

    pending = list_pending(DB_PATH)
    return render_template_string(ADMIN_PAGE, pending=pending, msg=msg)


@app.route("/admin/searches")
@admin_required
def admin_searches():
    """Admin review of every search run by approved users."""
    from credentials_db import list_search_logs, search_stats_by_user

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 100
    offset = (page - 1) * per_page
    user_filter = request.args.get("user", "").strip() or None
    query_filter = request.args.get("q", "").strip() or None

    rows, total = list_search_logs(
        DB_PATH,
        limit=per_page,
        offset=offset,
        user_filter=user_filter,
        query_filter=query_filter,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    distinct_users = search_stats_by_user(DB_PATH)
    distinct_queries = 0
    distinct_active = 0
    if rows:
        distinct_active = len({r["username"] for r in rows})
        distinct_queries = len({r["query"] for r in rows})
    return render_template_string(
        SEARCHES_PAGE,
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        user_filter=user_filter or "",
        query_filter=query_filter or "",
        distinct_users=distinct_active or len(distinct_users),
        distinct_queries=distinct_queries,
    )


@app.route("/admin/users")
@admin_required
def admin_users():
    """Admin view of every registered account with registration + last login."""
    from credentials_db import list_all_users

    users = list_all_users(DB_PATH)
    total = len(users)
    approved = sum(1 for u in users if u["approved"])
    pending = total - approved
    admins = sum(1 for u in users if u["is_admin"])
    return render_template_string(
        USERS_PAGE,
        users=users,
        total=total,
        approved=approved,
        pending=pending,
        admins=admins,
    )


# ── helper functions ────────────────────────────────────────────────────


# Wire the standalone visitor fingerprint collector.
# Separate DB (FP_DB_PATH / fingerprints.db), routes registered on this app:
#   POST /api/fingerprint        — collects every visitor (no auth)
#   GET  /admin/fingerprints     — viewer guarded by existing admin session

init_fingerprint_collector(
    app,
    admin_guard=lambda: bool(session.get("is_admin")),
    db_path=Path(os.getenv("CREDDB_FP_DB_PATH", "downloads/fingerprints.db")),
)


def authenticate(username: str, password: str) -> dict | None:
    from credentials_db import authenticate as db_auth

    return db_auth(username, password, DB_PATH)


def create_user(username: str, password: str) -> bool:
    from credentials_db import create_user as db_create

    return db_create(username, password, DB_PATH)


def get_stats():
    if not DB_PATH.exists():
        return {"total": 0, "domains": 0, "messages": 0}
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    domains = db.execute("SELECT COUNT(DISTINCT domain) FROM credentials").fetchone()[0]
    messages = db.execute("SELECT COUNT(DISTINCT msg_id) FROM credentials").fetchone()[
        0
    ]
    return {"total": total, "domains": domains, "messages": messages}


# ── main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from credentials_db import init_db, seed_admin

    init_db()
    if ADMIN_PASS:
        seed_admin(ADMIN_USER, ADMIN_PASS)
        print(f"Search server starting on 127.0.0.1:{PORT}  (admin: {ADMIN_USER})")
    else:
        print(
            f"Search server starting on 127.0.0.1:{PORT}  (CREDDB_WEB_ADMIN_PASS not set; admin not seeded)"
        )
    app.run(host="127.0.0.1", port=PORT, debug=False)
