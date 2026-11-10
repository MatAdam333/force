"""Sharing one session with another user of this host (SPEC §19).

Security model:
- A guest never gets access to cockpit's tmux server: tmux has no per-command permissions, so any
  client could run commands as the owner. Instead a per-(session, guest) proxy, owned by the owner,
  mirrors exactly ONE pane - the session's agent pane - over a Unix socket.
- The socket lives in /tmp/force-share-<owner>/ (mode 0711, owned by the owner, never a symlink).
  Every connection is checked with SO_PEERCRED: only the named guest's uid is served.
- Output: periodic `capture-pane -e` of that pane only (no sidebar, header or other session is ever
  read). Input (write shares only): bytes are delivered with `send-keys -H` to that pane only, so
  nothing reaches tmux key bindings or commands. Read-only shares drop all input.
Wire protocol (both directions): 1-byte type + 4-byte big-endian length + payload.
  client -> proxy: b"H" hello (JSON {"rows","cols"}), b"D" input bytes, b"R" resize (JSON),
                   b"Q" history request.
  proxy -> client: b"M" info (JSON), b"D" output bytes, b"T" history (JSON {"entries"}),
                   b"X" closing message (UTF-8 text).
History: Claude draws on the alternate screen, so the mirror has no scrollback. A guest's history
comes from THIS session's transcript (shells: the pane's own tmux scrollback), rendered as plain
text by cockpit.history - nothing else is read.
"""
from __future__ import annotations

import json
import os
import pwd
import selectors
import signal
import socket
import stat
import struct
import sys
import threading
import time
from pathlib import Path

from . import history, registry
from .claude import find_transcript
from .tmux import Tmux

MAX_FRAME = 64 * 1024
MAX_HISTORY = 4 * 1024 * 1024     # bytes of JSON per history answer
FPS = 8.0
_HDR = struct.Struct(">cI")


# --- paths --------------------------------------------------------------------------------------
def share_dir(owner: str | None = None) -> Path:
    owner = owner or pwd.getpwuid(os.getuid()).pw_name
    return Path(f"/tmp/force-share-{owner}")


def socket_path(name: str, guest: str, owner: str | None = None) -> Path:
    return share_dir(owner) / f"{name}.{guest}.sock"


def ensure_share_dir() -> Path:
    """Create /tmp/force-share-<me> (0711) or verify an existing one is ours, a real directory and not
    writable by others. Raises PermissionError otherwise (someone may have planted it)."""
    d = share_dir()
    try:
        d.mkdir(mode=0o711)
    except FileExistsError:
        pass
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
        raise PermissionError(f"{d} is not a directory owned by you; refusing to share")
    if st.st_mode & 0o022:
        raise PermissionError(f"{d} is writable by others; refusing to share")
    os.chmod(d, 0o711)
    return d


def guest_uid(guest: str) -> int:
    """uid of an existing account; KeyError if there is none."""
    return pwd.getpwnam(guest).pw_uid


def peer_uid(conn: socket.socket) -> int:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def peer_allowed(uid: int, allowed_uid: int) -> bool:
    """Only the named guest. (The owner never needs the proxy: they have force itself.)"""
    return uid == allowed_uid


# --- framing -----------------------------------------------------------------------------------
def frame(kind: bytes, payload: bytes) -> bytes:
    return _HDR.pack(kind, len(payload)) + payload


class FrameReader:
    """Incremental decoder; raises ValueError on an oversized or unknown frame."""

    def __init__(self, kinds: set[bytes]):
        self.buf = b""
        self.kinds = kinds

    def feed(self, data: bytes) -> list[tuple[bytes, bytes]]:
        self.buf += data
        out = []
        while len(self.buf) >= _HDR.size:
            kind, length = _HDR.unpack_from(self.buf)
            if kind not in self.kinds or length > MAX_FRAME:
                raise ValueError("bad frame")
            if len(self.buf) < _HDR.size + length:
                break
            out.append((kind, self.buf[_HDR.size:_HDR.size + length]))
            self.buf = self.buf[_HDR.size + length:]
        return out


# --- the pane mirror -----------------------------------------------------------------------------
def render(capture: str, cursor_x: int, cursor_y: int, cursor_on: bool) -> bytes:
    """One full frame for the guest terminal: synchronized update, home, the captured lines (each
    cleared to end of line), clear below, then the cursor where the pane has it."""
    lines = capture.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    # tmux carries attributes across lines in a capture, so lines are joined WITHOUT a reset; erase-
    # to-end-of-line then fills with the current background like the pane. No autowrap: a smaller
    # guest terminal crops instead of wrapping.
    body = "\x1b[K\r\n".join(lines)
    out = ("\x1b[?2026h\x1b[?7l\x1b[?25l\x1b[0m\x1b[H" + body + "\x1b[0m\x1b[K\x1b[J"
           + f"\x1b[{cursor_y + 1};{cursor_x + 1}H" + ("\x1b[?25h" if cursor_on else "")
           + "\x1b[?2026l")
    return out.encode("utf-8", errors="replace")


class PaneMirror:
    """Reads and writes exactly one pane: the role=agent pane of session `name`."""

    def __init__(self, tmux: Tmux, name: str, home: Path | None = None, claude_dir: Path | None = None):
        self.tmux = tmux
        self.name = name
        self.home = home                # FORCE_HOME (registry) and claude dir: for the history only
        self.claude_dir = claude_dir
        self._hist_lock = threading.Lock()
        self._hist_key = None
        self._hist: bytes = b""

    def pane(self) -> str | None:
        for p in self.tmux.panes():
            if p.session == self.name and p.role == "agent" and not p.dead:
                return p.pane_id
        return None

    def snapshot(self) -> bytes | None:
        pane = self.pane()
        if pane is None:
            return None
        info = self.tmux.run("display", "-p", "-t", pane,
                             "#{cursor_x} #{cursor_y} #{cursor_flag}", check=False).split()
        cap = self.tmux.run("capture-pane", "-p", "-e", "-N", "-t", pane, check=False)
        try:
            x, y, flag = int(info[0]), int(info[1]), info[2] == "1"
        except (IndexError, ValueError):
            x, y, flag = 0, 0, True
        return render(cap, x, y, flag)

    def history(self) -> bytes:
        """JSON {"entries": [[kind, text], ...]} for this session, at most MAX_HISTORY bytes (oldest
        entries dropped). Cached per transcript size/mtime, so repeated requests are cheap."""
        entries: list = []
        entry = None
        if self.home is not None:
            try:
                entry = registry.load(self.home / "registry.json").get(self.name)
            except registry.RegistryError:
                entry = None
        if entry is not None and registry.is_shell(entry):
            pane = self.pane()
            if pane is not None:
                text = self.tmux.run("capture-pane", "-p", "-J", "-S", "-5000", "-t", pane, check=False)
                entries = history.render_plain(text)
        elif entry is not None and self.claude_dir is not None:
            path = find_transcript(self.claude_dir, entry.session_id)
            if path is not None:
                try:
                    st = path.stat()
                    key = (str(path), st.st_size, st.st_mtime_ns)
                except OSError:
                    key = None
                with self._hist_lock:
                    if key is not None and key == self._hist_key:
                        return self._hist
                    entries = history.render_transcript(path)
                    out = _history_json(entries)
                    self._hist_key, self._hist = key, out
                    return out
        return _history_json(entries)

    def send(self, data: bytes) -> None:
        pane = self.pane()
        if pane is None or not data:
            return
        # -H: the bytes go to this pane's input as typed; no key-binding or command lookup.
        for i in range(0, len(data), 256):
            chunk = data[i:i + 256]
            self.tmux.run("send-keys", "-t", pane, "-H", *[f"{b:02x}" for b in chunk], check=False)


def _history_json(entries: list) -> bytes:
    out = json.dumps({"entries": entries}).encode()
    while len(out) > MAX_HISTORY and entries:
        entries = entries[max(1, len(entries) // 10):]
        out = json.dumps({"entries": [["note", "earlier history not shown"]] + entries}).encode()
    return out


def serve_connection(conn: socket.socket, mirror: PaneMirror, writable: bool,
                     fps: float = FPS) -> None:
    """Stream frames to one authenticated guest until either side closes. The first frame (b"M")
    tells the client what it is looking at, so it can show a status line and the right exit keys."""
    reader = FrameReader({b"H", b"D", b"R", b"Q"})
    owner = pwd.getpwuid(os.getuid()).pw_name
    try:
        conn.sendall(frame(b"M", json.dumps({"owner": owner, "session": mirror.name,
                                             "mode": "read/write" if writable else "read-only"}).encode()))
    except OSError:
        conn.close()
        return
    sel = selectors.DefaultSelector()
    conn.setblocking(False)
    sel.register(conn, selectors.EVENT_READ)
    last = None
    next_frame = 0.0
    try:
        while True:
            timeout = max(0.0, next_frame - time.monotonic())
            for _key, _ev in sel.select(timeout):
                try:
                    data = conn.recv(65536)
                except BlockingIOError:
                    continue
                if not data:
                    return
                for kind, payload in reader.feed(data):
                    if kind == b"D" and writable:
                        mirror.send(payload)
                    elif kind in (b"H", b"R"):
                        last = None              # (re)draw the full frame
                    elif kind == b"Q":
                        _send_all(conn, frame(b"T", mirror.history()))
            if time.monotonic() >= next_frame:
                next_frame = time.monotonic() + 1.0 / fps
                snap = mirror.snapshot()
                if snap is None:
                    _send_all(conn, frame(b"X", b"the shared session is not running"))
                    return
                if snap != last:
                    _send_all(conn, frame(b"D", snap))
                    last = snap
    except (ValueError, OSError):
        return
    finally:
        sel.close()
        try:
            conn.close()
        except OSError:
            pass


def _send_all(conn: socket.socket, data: bytes) -> None:
    conn.setblocking(True)
    try:
        conn.sendall(data)
    finally:
        conn.setblocking(False)


def run_proxy(tmux: Tmux, name: str, guest: str, mode: str,
              home: Path | None = None, claude_dir: Path | None = None) -> int:
    """`force share-proxy NAME GUEST r|w`: serve guests until killed (the daemon supervises it)."""
    allowed = guest_uid(guest)
    ensure_share_dir()
    # The daemon stops a proxy with SIGTERM: leave through `finally` so the socket file is removed.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    path = socket_path(name, guest)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    os.chmod(path, 0o666)          # anyone may connect; SO_PEERCRED decides who is served
    srv.listen(4)
    mirror = PaneMirror(tmux, name, home, claude_dir)
    writable = mode == "w"
    try:
        while True:
            conn, _ = srv.accept()
            if not peer_allowed(peer_uid(conn), allowed):
                try:
                    conn.sendall(frame(b"X", b"not shared with you"))
                finally:
                    conn.close()
                continue
            threading.Thread(target=serve_connection, args=(conn, mirror, writable),
                             daemon=True).start()
    finally:
        srv.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":     # pragma: no cover
    sys.exit(2)
