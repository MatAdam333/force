import time
import uuid
from pathlib import Path

from force import doctor
from force.registry import locked
from force.state import Snapshot, write_snapshot


def _uuid():
    return str(uuid.uuid4())


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


def _fresh_snapshot(env):
    write_snapshot(env.paths.snapshot, Snapshot(
        generated_ms=int(time.time() * 1000), latest_version=None, sessions=[]))


def _set_options(env):
    env.tmux.set_option("@force_state", str(env.paths.state))
    env.tmux.set_option("@force_app", str(Path(env.paths.tmux_conf).parent.parent))


def _healthy(env, name="portal"):
    cockpit = env.make_cockpit()
    with locked(env.paths.home) as reg:
        e = reg.add(name, _uuid(), "/tmp")
    cockpit.create(e)
    assert wait_for(lambda: cockpit.agent_pane(name) is not None)
    _set_options(env)
    _fresh_snapshot(env)
    return cockpit


def _codes(probs):
    return {p.code for p in probs}


# --- check -------------------------------------------------------------
def test_healthy_setup_has_no_problems(env):
    cockpit = _healthy(env)
    assert doctor.check(cockpit, systemd=False) == []


def test_missing_header_detected_and_repaired(env):
    cockpit = _healthy(env)
    header = next(p for p in cockpit.tmux.panes() if p.role == "header")
    cockpit.tmux.run("kill-pane", "-t", header.pane_id)
    claude_before = cockpit.agent_pane("portal").pid

    probs = doctor.check(cockpit, systemd=False)
    assert "header-missing" in _codes(probs)

    lines = doctor.repair(cockpit, probs)
    assert any("header-missing" in l for l in lines)
    assert doctor.check(cockpit, systemd=False) == []
    assert cockpit.agent_pane("portal").pid == claude_before


def test_duplicate_sidebar_detected_and_repaired(env):
    cockpit = _healthy(env)
    claude = cockpit.agent_pane("portal")
    cockpit.tmux.run("split-window", "-h", "-b", "-l", "10", "-t", claude.pane_id,
                 "sleep 100", ";", "set-option", "-p", "@force_role", "sidebar")
    claude_before = cockpit.agent_pane("portal").pid

    probs = doctor.check(cockpit, systemd=False)
    assert "sidebar-dup" in _codes(probs)

    doctor.repair(cockpit, probs)
    assert doctor.check(cockpit, systemd=False) == []
    assert cockpit.agent_pane("portal").pid == claude_before


def test_sidebar_in_stash_while_not_hidden_repaired(env):
    cockpit = _healthy(env)
    sb = next(p for p in cockpit.tmux.panes() if p.role == "sidebar")
    cockpit.tmux.run("break-pane", "-d", "-s", sb.pane_id, "-t", "=portal:", "-n", "force-stash")
    claude_before = cockpit.agent_pane("portal").pid

    probs = doctor.check(cockpit, systemd=False)
    assert "sidebar-placement" in _codes(probs)

    doctor.repair(cockpit, probs)
    assert doctor.check(cockpit, systemd=False) == []
    assert cockpit.agent_pane("portal").pid == claude_before


def test_sidebar_in_claude_window_while_hidden_repaired(env):
    cockpit = _healthy(env)
    cockpit.tmux.set_option("@force_sidebar_hidden", "1")
    claude_before = cockpit.agent_pane("portal").pid

    probs = doctor.check(cockpit, systemd=False)
    assert "sidebar-placement" in _codes(probs)

    doctor.repair(cockpit, probs)
    assert doctor.check(cockpit, systemd=False) == []
    assert cockpit.agent_pane("portal").pid == claude_before


def test_stray_window_detected_and_repaired(env):
    cockpit = _healthy(env)
    cockpit.tmux.run("new-window", "-d", "-t", "=portal:", "-n", "junk")
    claude_before = cockpit.agent_pane("portal").pid

    probs = doctor.check(cockpit, systemd=False)
    assert "stray-window" in _codes(probs)

    doctor.repair(cockpit, probs)
    assert doctor.check(cockpit, systemd=False) == []
    assert cockpit.agent_pane("portal").pid == claude_before


def test_missing_options_detected_and_repaired(env):
    cockpit = _healthy(env)
    # remove the options so they read as empty
    cockpit.tmux.run("set-option", "-gu", "@force_state")
    cockpit.tmux.run("set-option", "-gu", "@force_app")
    claude_before = cockpit.agent_pane("portal").pid

    probs = doctor.check(cockpit, systemd=False)
    assert _codes(probs) == {"options"}

    doctor.repair(cockpit, probs)
    assert doctor.check(cockpit, systemd=False) == []
    assert cockpit.agent_pane("portal").pid == claude_before


def test_no_session_detected_and_repaired(env):
    # a live "keeper" session is healthy (and starts the tmux server + sets global options);
    # "portal" is a live registry entry with no tmux session yet.
    cockpit = _healthy(env, name="keeper")
    with locked(env.paths.home) as reg:
        reg.add("portal", _uuid(), "/tmp")

    probs = doctor.check(cockpit, systemd=False)
    assert "no-session" in _codes(probs)
    assert any(p.session == "portal" for p in probs if p.code == "no-session")

    doctor.repair(cockpit, probs)
    assert cockpit.tmux.has_session("portal")
    assert doctor.check(cockpit, systemd=False) == []


def test_no_agent_detected_and_repaired(env):
    cockpit = _healthy(env)
    agent = cockpit.agent_pane("portal")
    cockpit.tmux.run("kill-pane", "-t", agent.pane_id)

    probs = doctor.check(cockpit, systemd=False)
    assert "no-agent" in _codes(probs)

    doctor.repair(cockpit, probs)
    assert cockpit.agent_pane("portal") is not None
    assert doctor.check(cockpit, systemd=False) == []


def test_orphan_session_reported_but_untouched(env):
    cockpit = _healthy(env)
    cockpit.tmux.run("new-session", "-d", "-s", "junkbox", "sleep 1000")

    probs = doctor.check(cockpit, systemd=False)
    orphans = [p for p in probs if p.code == "orphan-session"]
    assert orphans and orphans[0].session == "junkbox"
    assert not orphans[0].fixable

    assert doctor.repair(cockpit, probs) == []               # nothing repaired
    assert cockpit.tmux.has_session("junkbox")                # untouched


# --- run_doctor --------------------------------------------------------
def test_run_doctor_healthy_returns_zero(env):
    cockpit = _healthy(env)
    lines = []
    assert doctor.run_doctor(cockpit, out=lines.append, systemd=False) == 0
    assert lines and "healthy" in lines[-1]


def test_run_doctor_unfixed_problem_returns_one(env):
    cockpit = _healthy(env)
    cockpit.tmux.set_option("@force_sidebar_hidden", "1")     # placement problem
    lines = []
    assert doctor.run_doctor(cockpit, out=lines.append, systemd=False) == 1


def test_run_doctor_fix_returns_zero_and_prints_fixed(env):
    cockpit = _healthy(env)
    cockpit.tmux.set_option("@force_sidebar_hidden", "1")
    lines = []
    assert doctor.run_doctor(cockpit, fix=True, out=lines.append, systemd=False) == 0
    assert any(l.startswith("✓ ") for l in lines)


def test_empty_server_is_not_a_tmux_failure(env):
    """A fresh install's tmux server has no sessions; `list-panes -a` then answers "no current target"."""
    from force.doctor import check
    cockpit = env.make_cockpit()
    cockpit.tmux.start_server()
    assert cockpit.tmux.server_running()
    assert cockpit.tmux.panes(strict=True) == []
    assert not [p for p in check(cockpit, systemd=False) if p.code == "tmux"]


def test_remote_problems(env):
    """Remotes (SPEC §40): connected is fine, a version mismatch wants an upgrade, a disabled one is ignored,
    a remote with no status is offline/unknown."""
    from force import remote
    cockpit = env.make_cockpit()
    remote.save(env.paths, [remote.Remote("a", "a"), remote.Remote("b", "b"),
                            remote.Remote("c", "c", enabled=False), remote.Remote("d", "d")])
    remote.write_status(env.paths, "a", "connected", "host1")
    remote.write_status(env.paths, "b", "version", "protocol 0 there, 1 here")

    probs = doctor.check(cockpit, systemd=False)
    versions = [p for p in probs if p.code == "remote-version"]
    assert len(versions) == 1
    assert versions[0].session is None
    assert "b" in versions[0].detail and "force host upgrade b" in versions[0].detail
    # a connected and c disabled produce no remote problems
    assert not [p for p in probs if p.code in ("remote-version", "remote-offline")
               and p.detail.startswith("a:")]
    assert not [p for p in probs if p.code in ("remote-version", "remote-offline")
               and p.detail.startswith("c:")]
    # d has no status.json -> offline unknown
    offlines = [p for p in probs if p.code == "remote-offline"]
    d = [p for p in offlines if p.detail.startswith("d:")]
    assert d and "unknown" in d[0].detail


def test_broken_hosts_does_not_crash(env):
    """A broken hosts.json must not crash doctor: remote.load already swallows it, and doctor's own guard
    turns any unexpected raise into a 'remotes' problem."""
    from force import remote
    cockpit = env.make_cockpit()
    (remote.remotes_file(env.paths).parent).mkdir(parents=True, exist_ok=True)
    remote.remotes_file(env.paths).write_text("not [[valid json")
    probs = doctor.check(cockpit, systemd=False)     # must not crash
    assert not [p for p in probs if p.code in ("remote-version", "remote-offline")]
    # the defensive guard: if remote.load raises, doctor turns it into a 'remotes' problem, not a crash
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(remote, "load", lambda paths: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        probs = doctor.check(cockpit, systemd=False)
        assert "remotes" in _codes(probs)
    finally:
        monkeypatch.undo()
