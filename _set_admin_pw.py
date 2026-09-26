#!/usr/bin/env python3
"""Set/rotate the admin password for the cred-search UI.
Usage: python _set_admin_pw.py <newpassword>
Updates the existing admin user's hash in the local DB AND prints the SQL
to apply the same change on the VPS.
"""
import hashlib, hmac, os, sqlite3, sys
from pathlib import Path

DB = Path(r'C:\Users\Administrator\coding\telegram-channel-watcher\downloads\credentials.db')

def _hash_password(pw: str, salt: bytes | None = None) -> str:
    # replicate credentials_db._hash_password (PBKDF2 or sha256? read module to be sure)
    raise SystemExit('placeholder')
