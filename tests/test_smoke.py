"""force smoke (SPEC §37): the parts that need no real Claude Code / OpenCode. The run itself is exercised
against the real tools by `force smoke`."""
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

from force import remote, smoke


V = {"force": "r1", "claude": "2.1.308", "opencode": "1.19.3"}


def test_due():
    now = 1_000_000.0
    assert smoke.due(None, V, now) == "no earlier run"
    ok = {"ok": True, "at": now - 3600, "versions": V}
    assert smoke.due(ok, V, now) is None
    assert smoke.due(ok, dict(V, claude="2.1.309"), now) == "versions changed"
    assert smoke.due(ok, dict(V, force="r2"), now) == "versions changed"
    assert smoke.due(dict(ok, at=now - smoke.DUE_AFTER_S), V, now) == "daily run"
    failed = {"ok": False, "at": now - 3600, "versions": V}
    assert smoke.due(failed, V, now) is None, "a failure is retried after 6 h, not every hour"
    assert smoke.due(dict(failed, at=now - smoke.RETRY_FAILED_S), V, now) == "retry after a failure"


def test_due_logged_out():
    now = 1_000_000.0
    logged_fail = {"ok": False, "at": now - 3600, "versions": V,
                   "checks": [{"name": "claude: logged in", "ok": False}]}
    assert smoke.due(logged_fail, V, now, logged_in=lambda: True) == "logged in again"
    assert smoke.due(logged_fail, V, now, logged_in=lambda: False) is None
    assert smoke.due(logged_fail, V, now, logged_in=None) is None
    plain_fail = {"ok": False, "at": now - 3600, "versions": V,
                  "checks": [{"name": "claude: turn", "ok": False, "detail": "no WORKING"}]}

    def boom():
        raise AssertionError("logged_in must not be called for a non-login failure")

    assert smoke.due(plain_fail, V, now, logged_in=boom) is None


def test_reason():
    assert smoke.reason([]) is None
    assert smoke.reason(None) is None
    assert smoke.reason("not a list") is None
    assert smoke.reason([{"name": "claude: start", "ok": True, "detail": ""}]) is None
    assert smoke.reason([{"name": "claude: logged in", "ok": False}]) == smoke.LOGGED_OUT
    assert (smoke.reason([{"name": "claude: turn", "ok": False,
                          "detail": "● Login expired · Please run /login"}]) == smoke.LOGGED_OUT)
    assert (smoke.reason([{"name": "claude: turn", "ok": False, "detail": "no WORKING"}])
            == "Claude Code: no reply")
    # skips non-dict entries
    assert (smoke.reason(["junk", {"ok": True}, {"name": "claude: turn", "ok": False, "detail": "x"}])
            == "Claude Code: no reply")
    assert smoke.failed_phrase("opencode: needs you") == "OpenCode: missed a prompt"
    assert smoke.tool_call_as_text('<｜DSML｜tool_call>\n<｜DSML｜invoke name="bash">touch x</｜DSML｜invoke>')
    assert not smoke.tool_call_as_text("Done: I ran touch smoke-marker.")
    assert (smoke.reason([{"name": "opencode: needs you", "ok": False,
                           "detail": smoke.TOOL_CALLS_AS_TEXT + ": the model wrote its tool call as text"}])
            == "OpenCode: tool calls fail")
    assert smoke.failed_phrase("split: force watch") == "self-test: window failed"
    assert smoke.failed_phrase("codex: something new") == "codex: self-test failed"
    # a missing name falls back to "self-test failed"
    assert smoke.reason(["junk", {"ok": False}]) == "self-test failed"
    # first failure wins
    assert (smoke.reason([{"name": "claude: start", "ok": True},
                         {"name": "claude: logged in", "ok": False},
                         {"name": "claude: turn", "ok": False}]) == smoke.LOGGED_OUT)


def test_claude_logged_in(tmp_path):
    def script(text, rc=0):
        p = tmp_path / "claude"
        p.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({text!r})\nsys.exit({rc})\n")
        p.chmod(0o755)
        return p

    assert smoke.claude_logged_in(script('{"loggedIn": false}\n', 1)) is False
    assert smoke.claude_logged_in(script('{"loggedIn": true}\n', 0)) is True
    assert smoke.claude_logged_in(script("garbage\n", 0)) is None
    assert smoke.claude_logged_in(script('{"loggedIn": "yes"}\n', 0)) is None
    assert smoke.claude_logged_in(tmp_path / "missing") is None


def test_command_last_reason(tmp_path, capsys):
    from types import SimpleNamespace
    base = tmp_path / "smoke"
    base.mkdir()
    (base / "last.json").write_text(json.dumps({
        "ok": False, "at": time.time(), "duration_s": 5, "versions": V,
        "checks": [{"name": "claude: logged in", "ok": False, "s": 0, "detail": ""}]}))
    paths = SimpleNamespace(state=tmp_path)
    assert smoke.command(paths, None, if_due=False, last=True) == 1
    out = capsys.readouterr().out
    assert "reason: Claude Code logged out" in out


def test_claude_flow_logged_out(tmp_path):
    from types import SimpleNamespace
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\nimport sys\n"
                     "sys.stdout.write('{\"loggedIn\": false}\\n')\nsys.exit(1)\n")
    script.chmod(0o755)
    log = []
    r = smoke.Run.__new__(smoke.Run)
    r.live = SimpleNamespace(claude_bin=script)
    r.checks, r.log = [], SimpleNamespace(write=log.append, flush=lambda: None)
    r.say = lambda t: log.append(t)
    r.add_entry = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no entry created"))
    r.claude_flow()   # must stop before creating any session
    assert r.checks == [{"name": "claude: logged in", "ok": False, "s": 0,
                         "detail": "Claude Code is logged out: run claude, then /login"}]


def test_read_last_and_command_last(tmp_path, capsys):
    from types import SimpleNamespace
    base = tmp_path / "smoke"
    paths = SimpleNamespace(state=tmp_path)
    assert smoke.read_last(base) is None
    assert smoke.command(paths, None, if_due=False, last=True) == 1
    base.mkdir()
    (base / "last.json").write_text(json.dumps({
        "ok": False, "at": time.time(), "duration_s": 57, "versions": V,
        "checks": [{"name": "claude: start", "ok": True, "s": 2, "detail": ""},
                   {"name": "claude: turn", "ok": False, "s": 90, "detail": "no WORKING"}]}))
    assert smoke.command(paths, None, if_due=False, last=True) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "claude: turn - no WORKING" in out and "claude 2.1.308" in out
    (base / "last.json").write_text("not json")
    assert smoke.read_last(base) is None


def test_snapshot_and_doctor_report_a_failed_smoke(env):
    from force.daemon import Daemon
    from force.doctor import check
    from force.state import read_snapshot
    cockpit = env.make_cockpit()
    d = Daemon(cockpit)
    d.tick()
    assert read_snapshot(env.paths.snapshot).smoke is None
    base = env.paths.state / "smoke"
    base.mkdir()
    (base / "last.json").write_text(json.dumps({"ok": False, "at": 5.0, "checks": [
        {"name": "opencode: move", "ok": False}]}))
    d.tick()
    assert read_snapshot(env.paths.snapshot).smoke == {"ok": False, "at": 5.0,
                                                       "reason": "OpenCode: move failed"}
    probs = [p for p in check(cockpit, systemd=False) if p.code == "self-test"]
    assert probs and "opencode: move" in probs[0].detail
    assert probs[0].detail.startswith("last `force smoke` failed: OpenCode: move failed")
    (base / "last.json").write_text(json.dumps({"ok": True, "at": 6.0}))
    d.tick()
    assert read_snapshot(env.paths.snapshot).smoke == {"ok": True, "at": 6.0}
    assert not [p for p in check(cockpit, systemd=False) if p.code == "self-test"]


def test_claude_checks_skipped_without_claude(tmp_path):
    from types import SimpleNamespace
    log = []
    r = smoke.Run.__new__(smoke.Run)
    r.live = SimpleNamespace(claude_bin=tmp_path / "no-claude")
    r.checks, r.log = [], SimpleNamespace(write=log.append, flush=lambda: None)
    r.say = lambda t: log.append(t)
    r.claude_flow()
    assert r.checks == [{"name": "claude", "ok": True, "s": 0, "detail": "not installed: skipped"}]


def test_environment_sets_ui_socket(tmp_path):
    """The smoke run sets its own ui tmux socket so split checks never touch the live ui server."""
    from types import SimpleNamespace
    r = smoke.Run.__new__(smoke.Run)
    r.run_dir = tmp_path / "run"
    r.base = tmp_path
    r.work = tmp_path / "work"
    r.live = SimpleNamespace(home=tmp_path / "home", claude_dir=tmp_path / "claude",
                           claude_bin=tmp_path / "claude-bin")
    r.opencode_managed = False
    saved = {}
    r.run_dir.mkdir()
    r._environment()
    assert os.environ["FORCE_UI_SOCKET"] == f"forcetest-smoke-ui-{os.getpid()}"
    assert os.environ["FORCE_TMUX_SOCKET"] == f"forcetest-smoke-{os.getpid()}"
    assert (r.run_dir / "tmux-ui.conf").is_file()


def _splitcockpit_script(tmp, proto):
    """A launcher for the split smoke test: `run` starts an interactive shell, `nested` attaches to the host
    tmux (creating the nested client), sidebar/header sleep, `watch` serves hello + the host's snapshot."""
    lines = [
        f"#!{sys.executable}",
        "import json, os, sys, time",
        "argv = sys.argv[1:]",
        "if not argv:",
        "    sys.exit(2)",
        "cmd = argv[0]",
        "if cmd in ('sidebar', 'header'):",
        "    os.execvp('sleep', ['sleep', '1000000'])",
        "if cmd == 'nested':",
        "    sock = os.environ.get('FORCE_TMUX_SOCKET', '')",
        "    os.execvp('tmux', ['tmux', '-L', sock, 'attach', '-t', '=' + argv[1]])",
        "if cmd == 'run':",
        "    os.environ['FORCE_SESSION'] = argv[1]",
        "    os.execvpe('bash', ['bash', '-l'], dict(os.environ, TMUX='', TMUX_PANE=''))",
        f"if cmd == 'watch':",
        "    snap = os.path.join(os.environ['FORCE_STATE'], 'snapshot.json')",
        "    reg = os.path.join(os.environ['FORCE_HOME'], 'registry.json')",
        "    out = sys.stdout",
        "    out.write('{\"hello\":{\"proto\":%d,\"version\":\"x\",\"host\":\"h\",\"ui\":\"split\"}}\\n' "
        f"% {proto})",
        "    out.flush()",
        "    last = None",
        "    end = time.monotonic() + 25",
        "    while time.monotonic() < end:",
        "        try:",
        "            data = open(snap).read()",
        "            rdata = open(reg).read()",
        "        except OSError:",
        "            time.sleep(0.2); continue",
        "        key = (len(data), len(rdata))",
        "        if key != last:",
        "            try:",
        "                snapobj = json.loads(data)",
        "                regobj = json.loads(rdata)",
        "            except ValueError:",
        "                time.sleep(0.2); continue",
        "            out.write(json.dumps({'snapshot': snapobj, 'registry': regobj}, "
        "                               separators=(',', ':')) + '\\n')",
        "            out.flush()",
        "            last = key",
        "        time.sleep(0.2)",
        "    sys.exit(0)",
        "sys.exit(2)",
    ]
    script = tmp / "splitcockpit"
    script.write_text("\n".join(lines))
    script.chmod(0o755)
    return script


def test_split_flow_real(env, monkeypatch, tmp_path):
    """`Run.split_flow` for real: a shell session in the host, shown through a ui session's nested client
    (keys reach it), and `force watch` serving that host's snapshot. Claude/OpenCode flows are skipped."""
    monkeypatch.setenv("FORCE_SMOKE_FORCE", str(_splitcockpit_script(tmp_path, remote.PROTO)))
    monkeypatch.setattr(smoke.Run, "claude_flow", lambda self: None)
    monkeypatch.setattr(smoke.Run, "opencode_flow", lambda self: None)
    monkeypatch.setattr(smoke, "versions", lambda paths: {"force": "r", "claude": "2.0.0",
                                                        "opencode": "1.0.0"})
    log = io.StringIO()
    result = smoke.run(env.paths, env.cfg, log=log)
    split = [c for c in result["checks"] if c["name"].startswith("split:")]
    assert len(split) == 2, [c["name"] for c in result["checks"]]
    assert all(c["ok"] for c in split), [(c["name"], c["detail"]) for c in result["checks"]]
    assert result["ok"]
    # the host and ui smoke servers are gone after cleanup
    from force.tmux import Tmux
    assert not Tmux(f"forcetest-smoke-{os.getpid()}", None).server_running()
    assert not Tmux(f"forcetest-smoke-ui-{os.getpid()}", None).server_running()


class _Dialog:
    """Claude's trust dialog: the first Down is lost (drawn too late), the second selects Yes."""
    def __init__(self, lose=1):
        self.lose, self.yes, self.keys = lose, False, []

    def key(self, name, k):
        self.keys.append(k)
        if k == "Down":
            if self.lose:
                self.lose -= 1
            else:
                self.yes = True

    def screen(self, name):
        return "Quick safety check | No, exit | ❯ Yes, I trust this folder" if self.yes else \
            "Quick safety check | ❯ No, exit | Yes, I trust this folder"


def test_trust_presses_enter_only_once_yes_is_selected(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda s: None)
    clock = iter(range(0, 10_000))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock) * 0.5)
    d = _Dialog(lose=1)
    smoke.Run.trust(d, "s", "move")
    assert d.keys == ["Down", "Down", "Enter"], "Enter never lands on 'No, exit'"


def test_trust_gives_up_without_pressing_enter(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda s: None)
    clock = iter(range(0, 10_000))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock) * 0.5)
    d = _Dialog(lose=99)
    with pytest.raises(smoke.SmokeFail):
        smoke.Run.trust(d, "s", "move")
    assert "Enter" not in d.keys
