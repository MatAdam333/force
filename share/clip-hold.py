#!/usr/bin/python3
"""Hold text on this machine's X clipboard until another program takes it (SPEC §41), on the machines the ui
tmux runs where neither wl-copy, xclip nor xsel is installed. The ui tmux (split mode) pipes each copy here
through clip-set.sh. Like xclip it keeps owning the selection, and hands it on when a real copy happens
(clipboard owner-change): a GTK (X11, also XWayland) client can own the clipboard without a focused window.

Reads stdin, then detaches (fork) and runs a GTK main loop until the selection changes hands, a 24 h timeout
or SIGTERM. Importable without python3-gi: the gi import happens inside main().
"""
import fcntl
import os
import signal
import sys


def read_text(stream, limit=4 * 1024 * 1024):
    """The whole stream as str, or None when it is empty or bigger than ``limit`` (dropped, not truncated).
    Invalid utf-8 is replaced."""
    data = stream.read()
    if not data:
        return None
    if len(data) > limit:
        return None
    return data.decode("utf-8", "replace")


def _pidfile() -> str:
    run = os.environ.get("XDG_RUNTIME_DIR")
    if run:
        return os.path.join(run, "force-clip-hold.pid")
    return f"/tmp/force-clip-hold-{os.getuid()}.pid"


def _sigterm_previous(pidfile: str) -> None:
    """Stop the previous holder so it does not keep the selection we now own."""
    try:
        with open(pidfile) as fh:
            old = int(fh.read().strip())
    except (ValueError, OSError):
        return
    if old <= 0 or not os.path.exists(f"/proc/{old}/cmdline"):
        return
    try:
        with open(f"/proc/{old}/cmdline") as fh:
            cmd = fh.read()
    except OSError:
        return
    if "clip-hold.py" in cmd:
        try:
            os.kill(old, signal.SIGTERM)
        except ProcessLookupError:
            pass


def main() -> int:
    text = read_text(sys.stdin.buffer)
    if text is None:
        return 0
    os.environ["GDK_BACKEND"] = "x11"   # XWayland too: an X client can own the clipboard
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
    except (ImportError, ValueError):
        return 0

    pidfile = _pidfile()
    # one holder at a time: stopping the previous one and recording the new one happen under a lock, so two
    # copies at once (both copy paths fire) cannot both keep running
    lock = os.open(pidfile + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    _sigterm_previous(pidfile)
    pid = os.fork()
    if pid > 0:
        with open(pidfile, "w") as fh:
            fh.write(str(pid))
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
        return 0
    # child: detached, fully backgrounded
    os.close(lock)
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    try:
        if not Gtk.init_check():
            return 0
        for selection in (Gdk.SELECTION_CLIPBOARD, Gdk.SELECTION_PRIMARY):
            Gtk.Clipboard.get(selection).set_text(text, -1)

        def _check_owner() -> bool:
            if Gdk.selection_owner_get(Gdk.SELECTION_CLIPBOARD) is None:
                Gtk.main_quit()
            return False

        def _on_owner_change(_clip, *_args) -> bool:
            GLib.idle_add(_check_owner)
            return False

        def _quit(*_args) -> bool:          # GLib passes user data to the signal handler
            Gtk.main_quit()
            return False

        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).connect("owner-change", _on_owner_change)
        GLib.timeout_add_seconds(24 * 60 * 60, _quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, _quit, None)
        Gtk.main()
    finally:
        try:
            with open(pidfile) as fh:
                if fh.read().strip() == str(os.getpid()):
                    os.unlink(pidfile)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
