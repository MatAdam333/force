"""Tests for force/share.py (SPEC §19): framing, peer auth, share-dir hardening and render() output."""
import os
import struct

import pytest

from force import share
from force.share import FrameReader, MAX_FRAME, _HDR, peer_allowed, render, ensure_share_dir


# --- framing --------------------------------------------------------------------
def test_frame_round_trip():
    kind, payload = b"D", b"hello world"
    raw = share.frame(kind, payload)
    assert raw == _HDR.pack(kind, len(payload)) + payload
    r = FrameReader({b"D"})
    assert r.feed(raw) == [(b"D", b"hello world")]
    assert r.feed(b"") == []          # nothing left over


def test_frame_multiple_split_chunks():
    """Multiple frames in one feed, and a frame split across two feeds, both decode."""
    r = FrameReader({b"D", b"H"})
    assert r.feed(share.frame(b"D", b"a") + share.frame(b"H", b"b")) == [(b"D", b"a"), (b"H", b"b")]
    partial = share.frame(b"D", b"partial")
    r2 = FrameReader({b"D"})
    assert r2.feed(partial[:5]) == []
    assert r2.feed(partial[5:]) == [(b"D", b"partial")]
    assert r2.feed(b"") == []


def test_frame_oversized_raises():
    r = FrameReader({b"D"})
    with pytest.raises(ValueError):
        r.feed(_HDR.pack(b"D", MAX_FRAME + 1))


def test_frame_unknown_kind_raises():
    r = FrameReader({b"D"})
    with pytest.raises(ValueError):
        r.feed(share.frame(b"Z", b"x"))


# --- peer auth ------------------------------------------------------------------
def test_peer_allowed_only_exact_uid():
    assert peer_allowed(1000, 1000) is True
    assert peer_allowed(1001, 1000) is False
    assert peer_allowed(0, 1000) is False
    assert peer_allowed(1000, 1001) is False


# --- share dir hardening -----------------------------------------------------------
def test_ensure_share_dir_creates_0711(tmp_path, monkeypatch):
    d = tmp_path / "share"
    monkeypatch.setattr(share, "share_dir", lambda owner=None: d)
    assert ensure_share_dir() == d
    assert d.is_dir()
    assert (d.stat().st_mode & 0o777) == 0o711


def test_ensure_share_dir_accepts_own_dir(tmp_path, monkeypatch):
    d = tmp_path / "share"
    d.mkdir()
    os.chmod(d, 0o700)
    monkeypatch.setattr(share, "share_dir", lambda owner=None: d)
    assert ensure_share_dir() == d
    assert (d.stat().st_mode & 0o777) == 0o711     # normalised back to 0711


def test_ensure_share_dir_refuses_symlink(tmp_path, monkeypatch):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "share"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(share, "share_dir", lambda owner=None: link)
    with pytest.raises(PermissionError):
        ensure_share_dir()


def test_ensure_share_dir_refuses_other_owner(tmp_path, monkeypatch):
    d = tmp_path / "share"
    d.mkdir()
    base = os.stat(d)
    fake = os.stat_result((base.st_mode, base.st_ino, base.st_dev, base.st_nlink,
                         base.st_uid + 1, base.st_gid, base.st_size,
                         base.st_atime, base.st_mtime, base.st_ctime))
    monkeypatch.setattr(share, "share_dir", lambda owner=None: d)
    monkeypatch.setattr(share.os, "lstat", lambda p, fake=fake: fake)
    with pytest.raises(PermissionError):
        ensure_share_dir()


def test_ensure_share_dir_refuses_group_world_writable(tmp_path, monkeypatch):
    d = tmp_path / "share"
    d.mkdir()
    os.chmod(d, 0o777)          # group/world writable
    monkeypatch.setattr(share, "share_dir", lambda owner=None: d)
    with pytest.raises(PermissionError):
        ensure_share_dir()


# --- render() output --------------------------------------------------------------
def test_render_no_reset_between_lines():
    out = render("line1\nline2", 3, 4, False).decode()
    # sync-update on, autowrap off, cursor hidden, home
    assert out.startswith("\x1b[?2026h\x1b[?7l\x1b[?25l\x1b[0m\x1b[H")
    # lines are joined with erase-to-EOL + newline, NOT a per-line reset
    assert "line1\x1b[K\r\nline2" in out
    assert "\x1b[0m" not in out.split("line1", 1)[1].split("line2", 1)[0]
    # ends with the cursor position and sync-update off
    assert out.endswith("\x1b[5;4H\x1b[?2026l")
    assert "?25h" not in out             # cursor_on False


def test_render_cursor_on_and_position():
    out = render("hello", 7, 0, True).decode()
    assert "\x1b[1;8H" in out          # cursor_y+1, cursor_x+1
    assert out.endswith("\x1b[?25h\x1b[?2026l")


# --- history (Q -> T) ----------------------------------------------------------------
import json
import socket
import threading

from force import registry as _registry


class _FakeTmux:
    """Only what PaneMirror uses; records every tmux command so tests can see what was read."""

    def __init__(self, scrollback="", session="s"):
        self.calls = []
        self.scrollback = scrollback
        self.session = session

    def panes(self):
        from force.tmux import Pane
        return [Pane("%1", self.session, "agent", 1, False, None, None, None),
                Pane("%2", self.session, "sidebar", 2, False, None, None, None),
                Pane("%3", "other", "agent", 3, False, None, None, None)]

    def run(self, *args, check=True):
        self.calls.append(args)
        if args[0] == "capture-pane" and "-S" in args:
            return self.scrollback
        if args[0] == "capture-pane":
            return "live screen\n"
        if args[0] == "display":
            return "0 0 1"
        return ""


def _registry_with(home, name, sid, shell=False):
    with _registry.locked(home) as reg:
        e = reg.add(name, sid, "/tmp")
        if shell:
            e.extra["kind"] = "shell"


SID = "11111111-2222-3333-4444-555555555555"


def _transcript(claude_dir, sid, records):
    d = claude_dir / "projects" / "-tmp"
    d.mkdir(parents=True)
    (d / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


def test_history_from_this_sessions_transcript(tmp_path):
    home, cdir = tmp_path / "home", tmp_path / "claude"
    _registry_with(home, "s", SID)
    _transcript(cdir, SID, [
        {"type": "user", "message": {"content": "hello there"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi \x1b[31m!"}]}},
    ])
    other = "99999999-2222-3333-4444-555555555555"
    (cdir / "projects" / "-tmp" / f"{other}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "SECRET other session"}}) + "\n")
    m = share.PaneMirror(_FakeTmux(), "s", home, cdir)
    out = m.history()
    assert b"SECRET" not in out and b"\x1b" not in out and b"\\u001b" not in out
    assert json.loads(out)["entries"] == [["user", "hello there"], ["agent", "hi !"]]
    assert m.history() is out          # cached while the transcript is unchanged


def test_history_unknown_session_or_no_transcript_is_empty(tmp_path):
    home, cdir = tmp_path / "home", tmp_path / "claude"
    assert json.loads(share.PaneMirror(_FakeTmux(), "s", home, cdir).history()) == {"entries": []}
    _registry_with(home, "s", SID)
    assert json.loads(share.PaneMirror(_FakeTmux(), "s", home, cdir).history()) == {"entries": []}
    assert json.loads(share.PaneMirror(_FakeTmux(), "s").history()) == {"entries": []}


def test_history_shell_reads_only_its_own_pane(tmp_path):
    home = tmp_path / "home"
    _registry_with(home, "s", SID, shell=True)
    t = _FakeTmux(scrollback="$ ls\na b\n\n")
    out = json.loads(share.PaneMirror(t, "s", home, tmp_path).history())
    assert out["entries"] == [["plain", "$ ls"], ["plain", "a b"]]
    caps = [c for c in t.calls if c[0] == "capture-pane"]
    assert caps and all(c[c.index("-t") + 1] == "%1" for c in caps)


def test_history_json_is_capped(monkeypatch):
    monkeypatch.setattr(share, "MAX_HISTORY", 2000)
    out = share._history_json([["agent", "x" * 100]] * 200)
    assert len(out) <= 2000
    entries = json.loads(out)["entries"]
    assert entries[0] == ["note", "earlier history not shown"] and entries[-1] == ["agent", "x" * 100]


def test_serve_connection_answers_history_request(tmp_path):
    home, cdir = tmp_path / "home", tmp_path / "claude"
    _registry_with(home, "s", SID)
    _transcript(cdir, SID, [{"type": "user", "message": {"content": "q?"}}])
    a, b = socket.socketpair()
    m = share.PaneMirror(_FakeTmux(), "s", home, cdir)
    th = threading.Thread(target=share.serve_connection, args=(b, m, False), daemon=True)
    th.start()
    a.sendall(share.frame(b"H", b'{"rows":10,"cols":40}') + share.frame(b"Q", b"{}"))
    reader = FrameReader({b"M", b"D", b"T"})
    a.settimeout(5)
    got = {}
    while b"T" not in got:
        for kind, payload in reader.feed(a.recv(65536)):
            got[kind] = payload
    assert json.loads(got[b"T"])["entries"] == [["user", "q?"]]
    a.close()
    th.join(5)
