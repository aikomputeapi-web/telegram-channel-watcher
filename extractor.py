"""Password parsing + archive extraction (zips via pyzipper; .7z/.rar and any
zip pyzipper can't handle fall back to the 7-Zip binary if present)."""
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

try:
    import pyzipper
except ImportError:  # zip extraction then requires the 7-Zip binary
    pyzipper = None

log = logging.getLogger("extractor")

ARCHIVE_EXTS = {".zip", ".7z", ".rar"}

# matches ".pass: secret" / ".PASS： secret" (fullwidth colon tolerated)
PW_RE = re.compile(r"\.pass\s*[:\uff1a]\s*(.+)", re.IGNORECASE)

_SEVENZIP_CANDIDATES = ("7zz", "7z", "7za")


class ExtractionError(Exception):
    pass


def is_supported_archive(name: str) -> bool:
    return Path(name).suffix.lower() in ARCHIVE_EXTS


def password_candidates(text):
    """Likely passwords from a post body: everything after '.pass:' on that line,
    then (fallback) just the first whitespace-delimited token of it."""
    if not text:
        return []
    m = PW_RE.search(text)
    if not m:
        return []
    raw = m.group(1).strip().strip('"').strip("'").strip()
    if not raw:
        return []
    cands = [raw]
    first = raw.split()[0]
    if first != raw:
        cands.append(first)
    return cands


def find_7z():
    override = os.getenv("SEVENZIP")
    if override:
        return override if (shutil.which(override) or Path(override).is_file()) else None
    for name in _SEVENZIP_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    win = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"
    return str(win) if win.is_file() else None


def _try_zip(path: Path, password: str, dest: Path) -> bool:
    if pyzipper is None:
        return False
    try:
        with pyzipper.AESZipFile(path) as zf:
            zf.setpassword(password.encode("utf-8"))
            zf.extractall(dest)
        return True
    except Exception as e:
        log.debug("pyzipper pw %r failed: %s", password, e)
        return False


def _try_7z(binary: str, path: Path, password: str, dest: Path) -> bool:
    cmd = [binary, "x", "-y", "-bd", f"-p{password}", f"-o{dest}", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        raise ExtractionError(f"could not run 7-Zip ({binary}): {e}")
    if proc.returncode == 0:
        return True
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    log.debug("7z rc=%s: %s", proc.returncode, tail[-1] if tail else "")
    return False


def extract_archive(path, passwords, dest) -> str:
    """Extract archive `path` into directory `dest` using the first working
    password. Returns the password that worked; raises ExtractionError if none
    did (the caller should keep the archive for a later retry)."""
    path, dest = Path(path), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()

    if ext == ".zip":
        for pw in passwords:
            if _try_zip(path, pw, dest):
                return pw

    sevenzip = find_7z()
    if sevenzip:
        for pw in passwords:
            if _try_7z(sevenzip, path, pw, dest):
                return pw
        detail = "wrong password(s) or damaged archive"
    elif ext == ".zip":
        detail = "no working password and no 7-Zip binary (install 7-Zip or set SEVENZIP)"
    else:
        detail = f".{ext.lstrip('.')} archives need the 7-Zip binary (install 7-Zip/p7zip or set SEVENZIP)"
    raise ExtractionError(detail)
