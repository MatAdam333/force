"""`force update` and the daemon's background check for a newer Force (SPEC §3).

The download base is `FORCE_DOWNLOAD_BASE` or the first line of `<app>/share/download-base`; the running
version is `<app>/VERSION`. A release advertises the latest `VERSION="X.Y.Z"` at the top of its
`<base>/install.sh`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import force

# force update: a generous timeout and the 1 MB cap the brief allows
_MAX_BYTES = 1_000_000
_FETCH_TIMEOUT = 20
# the daemon's silent background check: shorter timeout, small file
_CHECK_TIMEOUT = 10
# the daemon's cadence: first check 2 min after start, then every 24 h
_FIRST_DELAY = 120
_INTERVAL = 24 * 3600

_VERSION_RE = re.compile(r'^\s*VERSION\s*=\s*"([^"]+)"\s*$')
_VER = re.compile(r"\d+\.\d+\.\d+")


def app_dir() -> Path:
    """The directory that contains the running `force` package's parent (the release)."""
    return Path(force.__file__).resolve().parent.parent


def download_base() -> str | None:
    """The download base for this build: env FORCE_DOWNLOAD_BASE, else the first line of
    `<app>/share/download-base`. None when missing, empty or the `https://example.invalid` placeholder."""
    env = os.environ.get("FORCE_DOWNLOAD_BASE", "").strip()
    if env and env != "https://example.invalid":
        return env
    try:
        line = (app_dir() / "share" / "download-base").read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    return line if line and line != "https://example.invalid" else None


def current_version() -> str | None:
    """`<app>/VERSION` (X.Y.Z), or None when it is missing (a dev/deploy.sh build)."""
    try:
        text = (app_dir() / "VERSION").read_text().strip()
    except OSError:
        return None
    return text if _VER.fullmatch(text) else None


def _tuple(version: str | None) -> tuple | None:
    if not version or not _VER.fullmatch(version):
        return None
    return tuple(int(p) for p in version.split("."))


def fetch_install(base: str, timeout: int) -> tuple[bytes | None, str | None]:
    """Fetch `<base>/install.sh` (<= 1 MB) -> (content, error). `content` None on any failure."""
    url = base.rstrip("/") + "/install.sh"
    try:
        # Force names itself: a site's bot check may refuse Python's default user agent ("Python-urllib")
        req = urllib.request.Request(url, headers={"User-Agent": f"force/{current_version() or 'dev'}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(_MAX_BYTES + 1)
    except Exception as e:
        return None, str(e)
    if len(data) > _MAX_BYTES:
        return None, f"{url}: install.sh is larger than 1 MB"
    return data, None


def parse_install_version(content: bytes) -> str | None:
    """The `VERSION="X.Y.Z"` line near the top of install.sh, or None."""
    head = content[:8192].decode("utf-8", "replace")
    for line in head.splitlines():
        m = _VERSION_RE.match(line.rstrip("\r"))
        if m and _VER.fullmatch(m.group(1)):
            return m.group(1)
    return None


def update_command(check: bool = False) -> int:
    """`force update [--check]`. Installs the newer release (runs its install.sh --no-fonts) or reports."""
    base = download_base()
    if base is None:
        print("force: this Force build has no download address (built without FORCE_DOWNLOAD_BASE); "
              "update it the way it was installed", file=sys.stderr)
        return 1
    cur = current_version()
    if cur is None:
        print("force: development build (deploy.sh): not updated")
        return 0
    content, err = fetch_install(base, _FETCH_TIMEOUT)
    if content is None:
        print(f"force: cannot fetch {base.rstrip('/')}/install.sh: {err}", file=sys.stderr)
        return 1
    latest = parse_install_version(content)
    if latest is None:
        print("force: no VERSION line in <base>/install.sh", file=sys.stderr)
        return 1
    if _tuple(latest) <= _tuple(cur):
        print(f"force: Force {cur} is up to date")
        return 0
    if check:
        print(f"force: update available: {cur} -> {latest} (run: force update)")
        return 0
    print(f"force: updating {cur} -> {latest} (sessions keep running)")
    tmp = _write_tmp(content)
    try:
        return subprocess.run(["sh", tmp, "--no-fonts"]).returncode
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _write_tmp(content: bytes) -> str:
    import tempfile
    fd, path = tempfile.mkstemp(prefix="force-update-", suffix=".sh")
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path


def check_daemon(state: Path) -> str | None:
    """One silent background check: fetch+parse the latest, write `<state>/update.json`
    `{"latest": "B", "checked_ms": …}`, return B. Any failure returns None and keeps the old file."""
    base = download_base()
    if base is None or current_version() is None:
        return None
    content, _err = fetch_install(base, _CHECK_TIMEOUT)
    if content is None:
        return None
    latest = parse_install_version(content)
    if latest is None:
        return None
    try:
        state.mkdir(parents=True, exist_ok=True)
        tmp = state / "update.json.tmp"
        tmp.write_text(json.dumps({"latest": latest, "checked_ms": int(time.time() * 1000)}))
        os.replace(tmp, state / "update.json")
    except OSError:
        return None
    return latest


def snapshot_update(state: Path) -> str | None:
    """The value for the snapshot's `"update"` key: the checked latest when it is newer than the running
    VERSION, else None (no key)."""
    if current_version() is None:
        return None
    try:
        data = json.loads((state / "update.json").read_text())
        latest = data.get("latest")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(latest, str):
        return None
    a, b = _tuple(latest), _tuple(current_version())
    if a is None or b is None or a <= b:
        return None
    return latest
