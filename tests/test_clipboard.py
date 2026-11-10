"""Clipboard (SPEC §41): share/clip-set.sh puts stdin on the local clipboard; share/clip-hold.py holds it via GTK."""
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SET = ROOT / "share" / "clip-set.sh"


def _sandbox(tmp_path, tools):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for name, body in tools.items():
        p = bindir / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)
    log = tmp_path / "log"
    log.write_text("")
    env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin", XDG_RUNTIME_DIR=str(tmp_path), CLIPLOG=str(log))
    return log, env


def _run(tmp_path, tools, text=b"", wayland="", display=""):
    log, env = _sandbox(tmp_path, tools)
    if display:
        env["DISPLAY"] = display
    else:
        env.pop("DISPLAY", None)
    if wayland:
        env["WAYLAND_DISPLAY"] = wayland
    else:
        env.pop("WAYLAND_DISPLAY", None)
    r = subprocess.run(["sh", str(SET)], input=text, env=env, capture_output=True, timeout=60)
    return r, log


def test_xclip_gets_both_selections_unicode_multiline(tmp_path):
    text = "héllo\nwörld".encode()
    r, log = _run(tmp_path, {"xclip": 'echo "$*" >> "$CLIPLOG"; cat "$4" >> "$CLIPLOG"'},
                  text=text, display=":99")
    assert r.returncode == 0, r.stderr
    out = log.read_text()
    assert "-selection clipboard -i " in out
    assert "-selection primary -i " in out
    assert "héllo" in out and "wörld" in out


def test_wl_copy_preferred_over_xclip(tmp_path):
    r, log = _run(tmp_path, {
        "wl-copy": 'echo "wl $*" >> "$CLIPLOG"; cat >> "$CLIPLOG"',
        "xclip": 'echo "xc $*" >> "$CLIPLOG"; cat >> "$CLIPLOG"',
    }, text=b"data", wayland="w", display=":99")
    assert r.returncode == 0, r.stderr
    out = log.read_text()
    assert "wl $*" not in out
    assert "--primary" in out and "wl " in out
    assert "xc " not in out          # xclip never called


def test_no_display_is_a_no_op(tmp_path):
    r, log = _run(tmp_path, {"xclip": 'echo called >> "$CLIPLOG"'}, text=b"x", wayland="", display="")
    assert r.returncode == 0, r.stderr
    assert log.read_text() == ""


def test_empty_stdin_calls_nothing(tmp_path):
    r, log = _run(tmp_path, {"xclip": 'echo called >> "$CLIPLOG"'}, text=b"", display=":99")
    assert r.returncode == 0, r.stderr
    assert log.read_text() == ""


def test_over_4_mib_is_dropped(tmp_path):
    r, log = _run(tmp_path, {"xclip": 'echo called >> "$CLIPLOG"'}, text=b"x" * (4 * 1024 * 1024 + 1),
                  display=":99")
    assert r.returncode == 0, r.stderr
    assert log.read_text() == ""


def test_force_clip_hold_override(tmp_path):
    out = tmp_path / "out.txt"
    env = dict(os.environ, PATH=f"{tmp_path / 'fakebin'}:/usr/bin:/bin",
              XDG_RUNTIME_DIR=str(tmp_path), DISPLAY=":99",
              FORCE_CLIP_HOLD=f"cat > '{out}'")
    r = subprocess.run(["sh", str(SET)], input="hold me ✓".encode(), env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert out.read_text() == "hold me ✓"


def _load_hold():
    loader = importlib.machinery.SourceFileLoader("clip_hold", str(ROOT / "share" / "clip-hold.py"))
    spec = importlib.util.spec_from_loader("clip_hold", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_hold_read_text():
    hold = _load_hold()
    assert hold.read_text(io.BytesIO("ünïcode\nline2".encode())) == "ünïcode\nline2"
    assert hold.read_text(io.BytesIO(b"")) is None                        # empty
    assert hold.read_text(io.BytesIO(b"xxxx"), limit=3) is None         # over the limit: dropped
    assert hold.read_text(io.BytesIO(b"a\xffb")) == "a�b"       # invalid utf-8 replaced


def test_ui_conf_sends_each_copy_once(tmp_path):
    """The ui tmux (share/tmux-ui.conf) pipes an application's OSC 52 copy and a copy-mode copy to
    clip-set.sh through the pane-set-clipboard hook, once each (no copy-command as well)."""
    conf = (ROOT / "share" / "tmux-ui.conf").read_text()
    assert "force-connect" not in conf and "set -s copy-command" not in conf
    app = tmp_path / "app"
    (app / "share").mkdir(parents=True)
    log = tmp_path / "copies.log"
    stub = app / "share" / "clip-set.sh"
    stub.write_text(f"#!/bin/sh\n{{ cat; echo; }} >> {log}\n")
    stub.chmod(0o755)
    sock = f"forcetest-uiclip-{os.getpid()}"
    t = ["tmux", "-L", sock]
    try:
        subprocess.run(t + ["-f", str(ROOT / "share" / "tmux-ui.conf"), "new-session", "-d", "-s", "c", "-x", "80",
                            "-y", "10", "sh"], check=True, capture_output=True, timeout=30)
        subprocess.run(t + ["set", "-g", "@force_app", str(app)], check=True, timeout=10)
        pane = subprocess.run(t + ["display", "-p", "-t", "c:", "#{pane_id}"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
        # an application's copy: OSC 52 with base64 of "osc-copy"
        subprocess.run(t + ["send-keys", "-t", pane, "-l", "printf '\\033]52;c;b3NjLWNvcHk=\\007'"], timeout=10)
        subprocess.run(t + ["send-keys", "-t", pane, "Enter"], timeout=10)
        assert _wait(lambda: log.exists() and "osc-copy" in log.read_text())
        # a copy-mode copy (what a mouse drag ends with)
        subprocess.run(t + ["send-keys", "-t", pane, "-l", "echo mode-copy-line"], timeout=10)
        subprocess.run(t + ["send-keys", "-t", pane, "Enter"], timeout=10)
        time.sleep(0.5)
        subprocess.run(t + ["copy-mode", "-t", pane, ";", "send-keys", "-X", "-t", pane, "search-backward",
                            "^mode-copy-line", ";", "send-keys", "-X", "-t", pane, "select-line", ";",
                            "send-keys", "-X", "-t", pane, "copy-pipe-and-cancel"], timeout=10)
        assert _wait(lambda: "mode-copy-line" in log.read_text())
        time.sleep(1)
        text = log.read_text()
        assert text.count("osc-copy") == 1 and text.count("mode-copy-line") == 1, text
    finally:
        subprocess.run(t + ["kill-server"], capture_output=True)


def _wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()
