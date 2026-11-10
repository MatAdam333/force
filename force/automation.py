"""Automation, reports, explain (SPEC §43).

`force prompt|wait|read|keys|report|explain`: the commands scripts and agents use instead of raw
`tmux send-keys`, plus the script-reported state and a way to see why a card shows what it shows.

All logic lives here; cli.py only wires the argparse parsers and dispatch. The daemon's snapshot
(snapshot.json, one tick ~1 s) drives states; a snapshot older than 10 s means the daemon is off.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from . import registry, report
from .tmux import TmuxError

# a snapshot older than this (ms) means the daemon is not running (SPEC §43.1)
FRESH_MS = 10_000
_POLL = 0.2
_SEND_LAG = 0.15


class CmdError(Exception):
    """A command failed: (code, message). Printed as `force: MESSAGE` (or an error object with --json)
    and the process exits 1."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class UsageError(Exception):
    """Bad usage: printed to stderr, process exits 2."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------- #
# state names
# --------------------------------------------------------------------------- #

STATE_NAMES = {
    "needs-you": "NEEDS_YOU",
    "working": "WORKING",
    "your-turn": "YOUR_TURN",
    "idle": "IDLE",
    "stale": "STALE",
    "down": "DOWN",
    "starting": "STARTING",
    "shell": "SHELL",
    "unknown": "UNKNOWN",
}

DEFAULT_UNTIL = ("YOUR_TURN", "IDLE", "NEEDS_YOU")


def parse_state(text: str) -> str:
    """Command-line state -> the snapshot's upper-case name; accepts upper case and `_` for `-`.
    Unknown raises ValueError (the caller turns it into a usage error)."""
    key = str(text).lower().replace("_", "-")
    if key not in STATE_NAMES:
        raise ValueError(text)
    return STATE_NAMES[key]


def _state_label(state: str | None) -> str:
    """'YOUR_TURN' -> 'your-turn', None -> 'unknown' (how a final state is printed)."""
    if state is None:
        return "unknown"
    return state.lower().replace("_", "-")


def _until_set(words: list[str]) -> set[str]:
    if not words:
        return set(DEFAULT_UNTIL)
    out: set[str] = set()
    for w in words:
        try:
            out.add(parse_state(w))
        except ValueError:
            raise UsageError(f"unknown state {w!r}")
    return out


# --------------------------------------------------------------------------- #
# keys alias (a pure function)
# --------------------------------------------------------------------------- #

def key_alias(word: str) -> str:
    """The tmux key name for `word` (SPEC §43.1), else the word unchanged."""
    w = word.lower()
    if w in ("esc", "escape"):
        return "Escape"
    if w in ("enter", "return"):
        return "Enter"
    if w == "tab":
        return "Tab"
    if w == "space":
        return "Space"
    if w in ("up", "down", "left", "right"):
        return w.capitalize()
    if w == "backspace":
        return "BSpace"
    if w == "shift+tab":
        return "BTab"
    if w.startswith("ctrl+"):
        return "C-" + word[5:]
    if w.startswith("c-"):
        return "C-" + word[2:]
    if w.startswith("alt+"):
        return "M-" + word[4:]
    if w.startswith("m-"):
        return "M-" + word[2:]
    return word


# --------------------------------------------------------------------------- #
# snapshot / registry helpers
# --------------------------------------------------------------------------- #

def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_snapshot(cockpit) -> dict | None:
    """The snapshot as RAW JSON, or None when missing/unreadable."""
    try:
        with open(cockpit.paths.snapshot) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _snap_session(snap: dict | None, name: str) -> dict | None:
    if not isinstance(snap, dict):
        return None
    for s in snap.get("sessions") or []:
        if isinstance(s, dict) and s.get("name") == name:
            return s
    return None


def _snap_state(snap: dict | None, name: str) -> str | None:
    sess = _snap_session(snap, name)
    return sess.get("state") if sess else None


def _fresh_snapshot(cockpit, clock=None) -> dict:
    """The snapshot, refusing with daemon_not_running when missing or older than FRESH_MS. Its age is wall-clock
    time (generated_ms is wall clock), never the injectable wait clock (monotonic by default)."""
    snap = _read_snapshot(cockpit)
    now_ms = _now_ms()
    if snap is None:
        raise CmdError("daemon_not_running", "no snapshot: is the daemon running?")
    age_ms = now_ms - snap.get("generated_ms", 0)
    if age_ms > FRESH_MS:
        raise CmdError("daemon_not_running", f"the daemon is not running (snapshot is {age_ms // 1000} s old)")
    return snap


def _require_live(cockpit, name) -> None:
    """no_session / archived refusal shared by the driving commands."""
    entry = registry.load(cockpit.paths.registry).get(name)
    if entry is None:
        raise CmdError("no_session", f"no session named {name!r}")
    if entry.archived:
        raise CmdError("archived", f"session {name!r} is archived")


def _require_agent(cockpit, name):
    """no_session / archived / not_agent refusal (for prompt)."""
    _require_live(cockpit, name)
    entry = registry.load(cockpit.paths.registry).get(name)
    if registry.kind(entry) == "shell":
        raise CmdError("not_agent", f"session {name!r} is a shell, not an agent")
    return entry


def _agent_pane_or_down(cockpit, name):
    pane = cockpit.agent_pane(name)
    if pane is None or pane.dead:
        raise CmdError("down", f"session {name!r} is down")
    return pane


class _Deadline(Exception):
    pass


def _wait_until(cockpit, name: str, state_set: set[str], deadline, clock, sleep):
    """Poll the snapshot every 0.2 s until the state is one of state_set; _Deadline when `clock()`
    passes `deadline` (None waits forever). Raises daemon_not_running if the snapshot goes stale."""
    while True:
        snap = _fresh_snapshot(cockpit, clock)
        st = _snap_state(snap, name)
        if st in state_set:
            return st, snap
        if deadline is not None and clock() >= deadline:
            raise _Deadline()
        sleep(_POLL)


def _success_output(cockpit, args, snap, state) -> int:
    """wait / prompt --wait success output: the final state name, or the session object with --json."""
    sess = _snap_session(snap, args.name) or {}
    if args.json:
        print(json.dumps({
            "name": args.name,
            "state": state,
            "activity": sess.get("activity"),
            "since_ms": sess.get("since_ms"),
        }))
    else:
        print(_state_label(state))
    return 0


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #

def _prompt_text(args) -> str:
    if list(args.text) == ["-"]:
        try:
            return sys.stdin.read()
        except OSError:
            return ""
    return " ".join(args.text)


def _send_text(cockpit, pane_id: str, text: str) -> None:
    buf = f"force-prompt-{os.getpid()}"
    subprocess.run(cockpit.tmux._cmd(["load-buffer", "-b", buf, "-"]),
                  input=text, text=True, check=True)
    cockpit.tmux.run("paste-buffer", "-p", "-r", "-d", "-b", buf, "-t", pane_id)
    time.sleep(_SEND_LAG)
    cockpit.tmux.run("send-keys", "-t", pane_id, "Enter")


def prompt_command(cockpit, args, clock=time.monotonic, sleep=time.sleep) -> int:
    """`force prompt NAME TEXT… [--wait] [--until S]… [--timeout SEC] [--json]`."""
    if args.until and not args.wait:
        raise UsageError("--until needs --wait")
    _require_agent(cockpit, args.name)
    text = _prompt_text(args)
    if not text:
        raise CmdError("empty_prompt", "empty prompt")
    until = _until_set(args.until) if args.wait else set()
    overall = None if args.timeout is None else clock() + args.timeout   # the timeout includes the send

    snap = _fresh_snapshot(cockpit, clock)
    state = _snap_state(snap, args.name)
    if state == "DOWN":
        raise CmdError("down", f"session {args.name!r} is down")
    if state == "STARTING":
        raise CmdError("not_ready", f"session {args.name!r} is starting")
    if state == "NEEDS_YOU":
        raise CmdError("blocked", f"session {args.name!r} needs you: answer the dialog with force keys")
    pane = _agent_pane_or_down(cockpit, args.name)
    _send_text(cockpit, pane.pane_id, text)
    if not args.wait:
        return 0
    return _prompt_wait(cockpit, args, until, overall, clock, sleep)


def _prompt_wait(cockpit, args, until, overall, clock, sleep) -> int:
    """After the send: activity (working / needs-you) within 5 s, then one of `until`."""
    phase1 = min(clock() + 5.0, overall) if overall is not None else clock() + 5.0
    snap = _fresh_snapshot(cockpit, clock)
    st = _snap_state(snap, args.name)
    if st not in {"WORKING", "NEEDS_YOU"}:
        try:
            st, snap = _wait_until(cockpit, args.name, {"WORKING", "NEEDS_YOU"}, phase1, clock, sleep)
        except _Deadline:
            if overall is not None and clock() >= overall:
                raise CmdError("timeout", f"timed out waiting for {args.name!r}")
            raise CmdError("prompt_stalled", f"{args.name!r} did not start working within 5 s")
    if st not in until:
        try:
            st, snap = _wait_until(cockpit, args.name, until, overall, clock, sleep)
        except _Deadline:
            raise CmdError("timeout", f"timed out waiting for {args.name!r}")
    return _success_output(cockpit, args, snap, st)


# --------------------------------------------------------------------------- #
# wait
# --------------------------------------------------------------------------- #

def wait_command(cockpit, args, clock=time.monotonic, sleep=time.sleep) -> int:
    """`force wait NAME [--until S]… [--timeout SEC] [--json]`."""
    _require_live(cockpit, args.name)
    until = _until_set(args.until)
    snap = _fresh_snapshot(cockpit, clock)
    st = _snap_state(snap, args.name)
    if st not in until:
        deadline = None if args.timeout is None else clock() + args.timeout
        try:
            st, snap = _wait_until(cockpit, args.name, until, deadline, clock, sleep)
        except _Deadline:
            raise CmdError("timeout", f"timed out waiting for {args.name!r}")
    return _success_output(cockpit, args, snap, st)


# --------------------------------------------------------------------------- #
# keys
# --------------------------------------------------------------------------- #

def keys_command(cockpit, args) -> int:
    """`force keys NAME KEY… [--literal]`."""
    _require_live(cockpit, args.name)
    pane = _agent_pane_or_down(cockpit, args.name)
    if args.literal:
        cockpit.tmux.run("send-keys", "-t", pane.pane_id, "-l", " ".join(args.keys))
    else:
        cockpit.tmux.run("send-keys", "-t", pane.pane_id, *[key_alias(k) for k in args.keys])
    return 0


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #

def read_command(cockpit, args) -> int:
    """`force read NAME [--lines N] [--ansi]`."""
    if args.lines < 1:
        raise UsageError("--lines must be >= 1")
    _require_live(cockpit, args.name)
    pane = _agent_pane_or_down(cockpit, args.name)
    cmd = ["capture-pane", "-p", "-J", "-t", pane.pane_id, "-S", f"-{args.lines}"]
    if args.ansi:
        cmd.insert(1, "-e")
    out = cockpit.tmux.run(*cmd)
    lines = out.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    print("\n".join(lines))
    return 0


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def _default_report_name(cockpit) -> str:
    """The Force session of the calling pane, or None when this is not one."""
    tmux_env = os.environ.get("TMUX")
    tpane = os.environ.get("TMUX_PANE")
    if not tmux_env or not tpane:
        raise CmdError("not_in_session", "not inside a Force session; give the session name")
    sock = tmux_env.split(",", 1)[0]
    try:
        host_sock = cockpit.tmux.run("display", "-p", "#{socket_path}")
    except TmuxError:
        host_sock = ""
    if not sock or sock != host_sock:
        raise CmdError("not_in_session", "not inside a Force session; give the session name")
    try:
        name = cockpit.tmux.run("display", "-p", "-t", tpane, "#{session_name}")
    except TmuxError:
        name = ""
    if not name:
        raise CmdError("not_in_session", "not inside a Force session; give the session name")
    return name


def report_command(cockpit, args, now_ms=None) -> int:
    """`force report [NAME] (--state S [--summary T] [--pid P] | --clear)`."""
    name = args.name or _default_report_name(cockpit)
    _require_live(cockpit, name)
    state = args.state.strip().lower().replace("_", "-") if args.state else None
    if args.clear or state == "idle":
        report.clear(cockpit.paths, name)
        return 0
    if state not in report.STATES:
        raise UsageError("--state must be working, needs-you, your-turn or idle (or --clear)")
    pid = args.pid if args.pid is not None else os.getppid()
    report.write(cockpit.paths, name, state, args.summary, pid, now_ms if now_ms is not None else _now_ms())
    return 0


# --------------------------------------------------------------------------- #
# explain
# --------------------------------------------------------------------------- #

def _fmt_since(since_ms, now_ms) -> str:
    if since_ms is None:
        return "-"
    return f"{max(0, int((now_ms - since_ms) / 1000))} s"


def _fmt_report(info) -> str:
    if info is None:
        return "none"
    s = info["state"]
    if info.get("summary"):
        s += f" ({info['summary']})"
    return s


def explain_command(cockpit, args, now_ms=None) -> int:
    """`force explain NAME [--lines N] [--json]`."""
    entry = registry.load(cockpit.paths.registry).get(args.name)
    if entry is None:
        raise CmdError("no_session", f"no session named {args.name!r}")
    now = now_ms if now_ms is not None else _now_ms()
    snap = _read_snapshot(cockpit)
    sess = _snap_session(snap, args.name)
    pane = cockpit.agent_pane(args.name)

    snap_age = None
    if snap is not None:
        snap_age = max(0, int((now - snap.get("generated_ms", now)) / 1000))
        if snap_age * 1000 > FRESH_MS:
            print(f"force: snapshot is {snap_age} s old", file=sys.stderr)

    report_info = None
    r = report.read(cockpit.paths, args.name)
    if r is not None:
        report_info = {"state": r.state, "summary": r.summary, "pid": r.pid, "at_ms": r.at_ms}

    command = None
    if pane is not None:
        try:
            command = cockpit.tmux.run("display", "-p", "-t", pane.pane_id, "#{pane_current_command}")
        except TmuxError:
            command = None

    screen: list[str] = []
    if pane is not None:
        try:
            raw = cockpit.tmux.run("capture-pane", "-p", "-J", "-t", pane.pane_id)
        except TmuxError:
            raw = ""
        screen = [l for l in raw.splitlines() if l.strip()][-args.lines:]

    if sess is None:
        kind = registry.kind(entry)
        state = None
        since_ms = None
        viewed = None
        activity = None
        title = None
        why = None
    else:
        kind = sess.get("kind") or registry.kind(entry)
        state = sess.get("state")
        since_ms = sess.get("since_ms")
        viewed = sess.get("viewed")
        activity = sess.get("activity")
        title = sess.get("title")
        why = sess.get("why")

    if args.json:
        print(json.dumps({
            "name": args.name,
            "kind": kind,
            "state": state,
            "since_ms": since_ms,
            "viewed": viewed,
            "activity": activity,
            "title": title,
            "why": why,
            "snapshot_age_s": snap_age,
            "pane": pane.pane_id if pane else None,
            "pid": pane.pid if pane else None,
            "command": command,
            "dead": pane.dead if pane else None,
            "report": report_info,
            "screen": screen,
        }))
        return 0

    rows = [
        ("kind", kind),
        ("state", _state_label(state) if state is not None else "-"),
        ("since", _fmt_since(since_ms, now)),
        ("viewed", viewed),
        ("activity", activity),
        ("title", title),
        ("why", why),
        ("snapshot age", f"{snap_age} s" if snap_age is not None else "-"),
        ("pane", pane.pane_id if pane else None),
        ("pid", pane.pid if pane else None),
        ("command", command),
        ("dead", pane.dead if pane else None),
        ("report", _fmt_report(report_info)),
    ]
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        print(f"{k:<{width}}: {v}")
    print("screen:")
    for line in screen:
        print(f"  {line}")
    return 0
