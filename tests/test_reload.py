import dataclasses
import json
import os
import signal
import time
import uuid

import pytest

from force.daemon import Daemon
from force.registry import locked
from force.tmux import Client


def _uuid():
    return str(uuid.uuid4())


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def current_pid(cockpit, name):
    p = cockpit.agent_pane(name)
    return p.pid if p else None


def add_live(cockpit, name, cwd="/tmp"):
    with locked(cockpit.paths.home) as reg:
        e = reg.add(name, _uuid(), cwd)
    cockpit.create(e)
    assert wait_for(lambda: current_pid(cockpit, name) is not None), f"{name} not running"
    return name


def sidebars(cockpit):
    return [p for p in cockpit.tmux.panes() if p.role == "sidebar"]


def agents(cockpit):
    return [p for p in cockpit.tmux.panes() if p.role == "agent"]


class FakeClock:
    def __init__(self, start=None):
        self.t = start if start is not None else time.time()

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _bump(path, mtime=None):
    """Rewrite an existing file with a different mtime so the fingerprint changes."""
    base = os.stat(path).st_mtime_ns
    with open(path, "a") as f:
        f.write("\n# bump\n")
    os.utime(path, ns=(base + 1_000_000_000, base + 1_000_000_000))


def _watch(tmp):
    d = tmp / "watch"
    d.mkdir(exist_ok=True)
    (d / "mod.py").write_text("x = 1\n")
    return d


def _attach_fake_client(cockpit, monkeypatch):
    messages = []
    monkeypatch.setattr(cockpit.tmux, "display_message",
                       lambda client, text, d=None: messages.append(text))
    monkeypatch.setattr(cockpit.tmux, "clients",
                      lambda: [Client(name="cc", session="portal", width=0, height=0, activity=0)])
    return messages


def test_no_reload_first_tick_then_reloads(env, monkeypatch):
    cockpit = env.make_cockpit()
    add_live(cockpit, "portal")
    cl = agents(cockpit)[0]
    messages = _attach_fake_client(cockpit, monkeypatch)
    watch = _watch(env.tmp)
    clock = FakeClock()
    validations = []
    reexecs = []
    events = []

    def validator(cp, cc):
        validations.append((cp, cc))
        events.append("validate")
        return None

    def reexec():
        reexecs.append(1)
        events.append("reexec")

    d = Daemon(cockpit, clock, watch_dir=watch, validator=validator, reexec=reexec)
    d.tick()  # first tick: baseline, no reload
    assert validations == [] and reexecs == []
    old_sb = sidebars(cockpit)[0]

    _bump(watch / "mod.py")
    d.tick()                          # notice the change, start debounce
    assert validations == [] and reexecs == []
    d.clock.advance(1.0)
    d.tick()                          # less than 2 s -> still nothing
    assert validations == [] and reexecs == []
    d.clock.advance(2.0)
    d.tick()                          # debounce passed -> validate, respawn, message, reexec
    assert validations == [(True, False)]
    assert reexecs == [1]
    assert events == ["validate", "reexec"]
    assert messages == []   # a successful reload is silent (no toast)
    # sidebar pane respawned (same pane id, new pid), agent pane untouched
    assert wait_for(lambda: sidebars(cockpit) and sidebars(cockpit)[0].pid != old_sb.pid)
    new_sb = sidebars(cockpit)[0]
    assert new_sb.pane_id == old_sb.pane_id
    assert agents(cockpit)[0].pid == cl.pid


def test_validator_error_skips_and_does_not_revalidate(env, monkeypatch):
    cockpit = env.make_cockpit()
    add_live(cockpit, "portal")
    sb = sidebars(cockpit)[0]
    cl = agents(cockpit)[0]
    messages = _attach_fake_client(cockpit, monkeypatch)
    watch = _watch(env.tmp)
    clock = FakeClock()
    validations = []
    reexecs = []
    d = Daemon(cockpit, clock, watch_dir=watch,
               validator=lambda cp, cc: (validations.append((cp, cc)) or "syntax boom"),
               reexec=lambda: reexecs.append(1))
    d.tick()

    f = watch / "mod.py"
    _bump(f)
    d.tick()
    d.clock.advance(3.0)
    d.tick()
    assert validations == [(True, False)]
    assert reexecs == []
    assert any("reload skipped" in m for m in messages)
    assert sidebars(cockpit)[0].pid == sb.pid
    assert agents(cockpit)[0].pid == cl.pid

    # same fingerprint is not re-validated on later ticks
    d.clock.advance(3.0)
    d.tick()
    d.clock.advance(3.0)
    d.tick()
    assert validations == [(True, False)]

    # a further change triggers a new validation
    _bump(f)
    d.tick()
    d.clock.advance(3.0)
    d.tick()
    assert validations == [(True, False), (True, False)]


def test_conf_only_change_sources_and_no_reexec(env, monkeypatch):
    cockpit = env.make_cockpit()
    add_live(cockpit, "portal")
    messages = _attach_fake_client(cockpit, monkeypatch)
    watch = _watch(env.tmp)
    clock = FakeClock()
    validations = []
    reexecs = []
    run_calls = []
    orig_rc = cockpit.tmux._run_rc
    monkeypatch.setattr(cockpit.tmux, "_run_rc",
                      lambda args: (run_calls.append(args) if args and args[0] == "source-file" else None) or orig_rc(args))
    d = Daemon(cockpit, clock, watch_dir=watch,
               validator=lambda cp, cc: (validations.append((cp, cc)) or None),
               reexec=lambda: reexecs.append(1))
    d.tick()
    _bump(env.paths.tmux_conf)
    d.tick()
    d.clock.advance(3.0)
    d.tick()
    assert validations == [(False, True)]
    assert reexecs == []
    assert any(a[0] == "source-file" for a in run_calls)
    assert messages == []


def test_a_conf_command_error_still_reloads_the_ui_conf_and_reexecs(env, monkeypatch):
    """tmux runs a whole config file even when one command in it fails, then exits 1 (e.g. `unbind -a` of a table
    that no longer exists). The daemon logs that and carries on: the ui config is sourced and the code re-execs."""
    cockpit = env.make_cockpit()
    add_live(cockpit, "portal")
    _attach_fake_client(cockpit, monkeypatch)
    watch = _watch(env.tmp)
    env.paths.ui_conf.parent.mkdir(parents=True, exist_ok=True)
    if not env.paths.ui_conf.exists():
        env.paths.ui_conf.write_text("# ui\n")
    host_calls, ui_calls, reexecs = [], [], []
    orig_rc = cockpit.tmux._run_rc
    monkeypatch.setattr(cockpit.tmux, "_run_rc", lambda args: (host_calls.append(args), (1, "", "table prefix doesn't exist"))[1]
                        if args[0] == "source-file" else orig_rc(args))
    monkeypatch.setattr(cockpit.ui_tmux, "server_running", lambda: True)
    monkeypatch.setattr(cockpit.ui_tmux, "_run_rc", lambda args: (ui_calls.append(args), (0, "", ""))[1])
    d = Daemon(cockpit, FakeClock(), watch_dir=watch, validator=lambda cp, cc: None, reexec=lambda: reexecs.append(1))
    d.tick()
    _bump(env.paths.tmux_conf)
    _bump(watch / "mod.py")
    d.tick()
    d.clock.advance(3.0)
    d.tick()
    assert host_calls and host_calls[0][0] == "source-file"
    assert [a for a in ui_calls if a[0] == "source-file"] == [["source-file", str(env.paths.ui_conf)]]
    assert reexecs == [1]


def test_dev_reload_disabled_does_nothing(env, monkeypatch):
    env.cfg = dataclasses.replace(env.cfg, dev_reload=False)
    cockpit = env.make_cockpit()
    add_live(cockpit, "portal")
    watch = _watch(env.tmp)
    clock = FakeClock()
    validations = []
    reexecs = []
    d = Daemon(cockpit, clock, watch_dir=watch,
               validator=lambda cp, cc: (validations.append((cp, cc)) or None),
               reexec=lambda: reexecs.append(1))
    d.tick()
    _bump(watch / "mod.py")
    d.tick()
    d.clock.advance(3.0)
    d.tick()
    assert validations == [] and reexecs == []


def test_dead_sidebar_respawned_at_most_once_per_10s(env):
    cockpit = env.make_cockpit()
    add_live(cockpit, "portal")
    clock = FakeClock()
    d = Daemon(cockpit, clock)
    d._sidebar_respawn = {}          # fresh, so the baseline tick does not rate-limit us
    d.tick()
    sb = sidebars(cockpit)[0]
    os.kill(sb.pid, signal.SIGKILL)          # remain-on-exit on -> pane stays behind, dead
    assert wait_for(lambda: sidebars(cockpit) and any(p.dead for p in sidebars(cockpit)))
    # dead agent pane must never be touched by this code path (crash step is the only one)
    cl = agents(cockpit)[0]
    os.kill(cl.pid, signal.SIGKILL)
    assert wait_for(lambda: agents(cockpit) and any(p.dead for p in agents(cockpit)))
    cl_pid = cl.pid

    d._step_sidebar()               # respawn the dead sidebar
    assert wait_for(lambda: sidebars(cockpit) and not sidebars(cockpit)[0].dead)
    assert sidebars(cockpit)[0].pid != sb.pid
    # agent pane untouched by sidebar step
    assert agents(cockpit)[0].dead and agents(cockpit)[0].pid == cl_pid

    # kill the new sidebar again; within 10 s it is NOT respawned
    sb2 = sidebars(cockpit)[0]
    os.kill(sb2.pid, signal.SIGKILL)
    assert wait_for(lambda: sidebars(cockpit) and any(p.dead for p in sidebars(cockpit)))
    d._step_sidebar()
    pid_after = [p for p in sidebars(cockpit)][0].pid
    assert [p for p in sidebars(cockpit)][0].dead and pid_after == sb2.pid   # unchanged within 10 s

    # after >10 s it is respawned again
    d.clock.advance(11.0)
    d._step_sidebar()
    assert wait_for(lambda: sidebars(cockpit) and not sidebars(cockpit)[0].dead)
    assert sidebars(cockpit)[0].pid != sb2.pid


def test_symlink_watch_swap_detected(env, monkeypatch):
    cockpit = env.make_cockpit()
    add_live(cockpit, "portal")
    dirA = env.tmp / "A"
    dirB = env.tmp / "B"
    dirA.mkdir()
    dirB.mkdir()
    fa = dirA / "m.py"
    fb = dirB / "m.py"
    fa.write_text("a" * 10)
    fb.write_text("b" * 20)                  # different size so the fingerprints differ
    os.utime(fa, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    os.utime(fb, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    watch = env.tmp / "watchlink"
    watch.symlink_to(dirA)
    clock = FakeClock()
    validations = []
    d = Daemon(cockpit, clock, watch_dir=watch,
               validator=lambda cp, cc: (validations.append((cp, cc)) or None),
               reexec=lambda: None)
    d.tick()                       # baseline through symlink -> dirA
    # re-point the symlink to a different release dir
    os.unlink(watch)
    watch.symlink_to(dirB)
    d.tick()                      # observe the swap
    d.clock.advance(3.0)
    d.tick()                      # trigger
    assert validations == [(True, False)]


def test_config_change_reexecs_and_respawns_ui(env, monkeypatch):
    """settings.json is read only at start: creating or changing it re-execs the daemon and respawns the
    sidebars (their flags come from it); Claude panes stay. No validation (load_config tolerates all)."""
    cockpit = env.make_cockpit()
    add_live(cockpit, "portal")
    cl = agents(cockpit)[0]
    _attach_fake_client(cockpit, monkeypatch)
    watch = _watch(env.tmp)
    clock = FakeClock()
    validations, reexecs = [], []
    d = Daemon(cockpit, clock, watch_dir=watch,
               validator=lambda cp, cc: (validations.append((cp, cc)) or None),
               reexec=lambda: reexecs.append(1))
    d.tick()
    old_sb = sidebars(cockpit)[0]
    env.paths.config_file.write_text('{"animations": "subtle"}')      # created
    d.tick()
    d.clock.advance(3.0)
    d.tick()
    assert reexecs == [1]
    assert validations == [(False, False)]
    assert wait_for(lambda: sidebars(cockpit) and sidebars(cockpit)[0].pid != old_sb.pid)
    assert agents(cockpit)[0].pid == cl.pid
    _bump(env.paths.config_file)                                      # changed
    d.tick()
    d.clock.advance(3.0)
    d.tick()
    assert reexecs == [1, 1]
