#!/usr/bin/env python3
"""Flask web UI for searching the credentials SQLite DB with auth.

Environment:
    CREDDB_WEB_ADMIN_USER  — admin username (default: admin)
    CREDDB_WEB_ADMIN_PASS  — admin password (no default; must be set in .env)
    CREDDB_DB_PATH         — path to credentials.db (default: downloads/credentials.db)
    CREDDB_PORT            — listen port (default: 5000)
"""
from __future__ import annotations

import functools
import os
import sqlite3
from pathlib import Path

from flask import Flask, request, redirect, url_for, session, render_template_string, g

app = Flask(__name__)
app.secret_key = os.urandom(32)

DB_PATH = Path(os.getenv("CREDDB_DB_PATH", "downloads/credentials.db"))
ADMIN_USER = os.getenv("CREDDB_WEB_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("CREDDB_WEB_ADMIN_PASS", "")
PORT = int(os.getenv("CREDDB_PORT", "5000"))
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
</div>
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
</div>
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
.btn { padding: 10px 16px; font-size: 15px; border-radius: 6px; text-decoration: none;
       cursor: pointer; font-weight: 600; border: none; }
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
td.password { color: #f0883e; font-family: monospace; }
td.username { color: #7ee787; font-family: monospace; }
.pagination { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
.pagination a, .pagination span { padding: 6px 12px; border-radius: 4px;
    text-decoration: none; font-size: 14px; }
.pagination a { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.pagination a:hover { background: #30363d; }
.pagination .current { background: #1f6feb; color: #fff; }
.empty { text-align:center; padding:40px; color:#8b949e; }
.qs { font-size: 12px; color: #8b949e; margin-bottom: 12px; }
.qs code { background: #21262d; padding: 2px 6px; border-radius: 3px; color: #7ee787; }
</style>
</head>
<body>
<h1>Credential Search</h1>
<div class="nav">
  <span class="user">{{ session.user }}</span>
  {% if is_admin %}<a class="admin-link" href="/admin">Admin</a>{% endif %}
  <a href="/logout">Logout</a>
</div>
<div class="stats">
  <div class="stat-item"><div class="stat-num">{{ stats.total }}</div><div class="stat-label">Credentials</div></div>
  <div class="stat-item"><div class="stat-num">{{ stats.domains }}</div><div class="stat-label">Domains</div></div>
  <div class="stat-item"><div class="stat-num">{{ stats.messages }}</div><div class="stat-label">Messages</div></div>
</div>
<div class="search-bar">
  <form method="GET">
    <input type="text" name="q" value="{{ query or '' }}" placeholder="Search domain, username, password, or origin..." autofocus>
    <button class="btn btn-primary" type="submit">Search</button>
  </form>
  <a class="btn btn-muted" href="?q=">Reset</a>
</div>
<div class="qs">
  Quick: <code>gmail.com</code> <code>facebook</code> <code>password</code> — matches domain, username, password, and origin.
</div>
{% if results %}
<table>
  <tr><th>#</th><th>Domain</th><th>Username</th><th>Password</th><th>Browser</th><th>Msg</th><th>Source File</th></tr>
  {% for r in results %}
  <tr>
    <td>{{ offset + loop.index }}</td>
    <td class="domain">{{ r.domain }}</td>
    <td class="username">{{ r.username or '—' }}</td>
    <td class="password">{{ r.password }}</td>
    <td>{{ r.browser or '—' }}</td>
    <td>{{ r.msg_id }}</td>
    <td style="font-size:11px;color:#8b949e;">{{ (r.source_file or '—')[:50] }}</td>
  </tr>
  {% endfor %}
</table>
<div class="pagination">
  {% if page > 1 %}<a href="?q={{ query }}&page={{ page - 1 }}">Prev</a>{% endif %}
  <span class="current">Page {{ page }} of {{ total_pages }}</span>
  {% if page < total_pages %}<a href="?q={{ query }}&page={{ page + 1 }}">Next</a>{% endif %}
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
<div class="nav"><a href="/">Back to Search</a> | <a href="/logout">Logout</a></div>
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


def login_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return render_template_string(LOGIN_PAGE, msg="Please sign in", msg_type="error")
        return f(*args, **kwargs)
    return wrapped


def admin_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return render_template_string(LOGIN_PAGE, msg="Please sign in", msg_type="error")
        if not session.get("is_admin"):
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapped


def get_db():
    if not hasattr(g, "_db"):
        g._db = sqlite3.connect(str(DB_PATH))
        g._db.row_factory = sqlite3.Row
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
            return render_template_string(LOGIN_PAGE, msg="Username and password required", msg_type="error")
        user = authenticate(u, p)
        if user:
            session["user"] = user["username"]
            session["is_admin"] = bool(user["is_admin"])
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        return render_template_string(LOGIN_PAGE, msg="Invalid username or password", msg_type="error")
    if session.get("user"):
        return redirect(url_for("index"))
    return render_template_string(LOGIN_PAGE, msg=None)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if not u or not p:
            return render_template_string(REGISTER_PAGE, msg="All fields required", msg_type="error")
        if len(p) < 4:
            return render_template_string(REGISTER_PAGE, msg="Password must be at least 4 characters", msg_type="error")
        if not create_user(u, p):
            return render_template_string(REGISTER_PAGE, msg="Username already taken", msg_type="error")
        return render_template_string(REGISTER_PAGE, msg="Account created — admin must approve before you can sign in", msg_type="success")
    return render_template_string(REGISTER_PAGE, msg=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── dashboard ───────────────────────────────────────────────────────────

@app.route("/search")
@login_required
def index():
    from credentials_db import search as db_search

    query = request.args.get("q", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    offset = (page - 1) * PER_PAGE

    s = get_stats()
    results, total = [], 0
    if query:
        results, total = db_search(DB_PATH, query, PER_PAGE, offset)

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return render_template_string(
        DASHBOARD,
        stats=s, query=query, results=results, total=total,
        page=page, total_pages=total_pages, offset=offset,
        is_admin=session.get("is_admin"),
    )


# ── admin ───────────────────────────────────────────────────────────────

@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    from credentials_db import list_pending_users as list_pending, approve_user as approve, reject_user as reject

    msg = None
    if request.method == "POST":
        user_id = int(request.form.get("user_id", 0))
        action = request.form.get("action", "")
        if user_id and action == "approve":
            approve(user_id, DB_PATH)
            msg = "User approved"
        elif user_id and action == "reject":
            reject(user_id, DB_PATH)
            msg = "User rejected"

    pending = list_pending(DB_PATH)
    return render_template_string(ADMIN_PAGE, pending=pending, msg=msg)


# ── helpers ─────────────────────────────────────────────────────────────

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
    messages = db.execute("SELECT COUNT(DISTINCT msg_id) FROM credentials").fetchone()[0]
    return {"total": total, "domains": domains, "messages": messages}


# ── main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from credentials_db import init_db, seed_admin
    init_db()
    if ADMIN_PASS:
        seed_admin(ADMIN_USER, ADMIN_PASS)
        print(f"Search server starting on 127.0.0.1:{PORT}  (admin: {ADMIN_USER})")
    else:
        print(f"Search server starting on 127.0.0.1:{PORT}  (CREDDB_WEB_ADMIN_PASS not set; admin not seeded)")
    app.run(host="127.0.0.1", port=PORT, debug=False)
