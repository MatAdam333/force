"""Tests for force/importer.py (SPEC §11). No real screen, tmux, Claude or ~/.claude."""
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from force.claude import AgentStatus
from force.importer import (Candidate, ancestors, children_map, descendants,
                         find_candidates, import_session, is_claude_pid,
                         parse_screen_ls, run_import, run_list)
from force.registry import Entry, Registry, load, save

SCREEN_OUT = "\t111111.webshop\t(10/07/2026 09:29:26 AM)\t(Attached)\n"
SID = "94dfcb67-2805-4385-be2a-090a17d98fca"


def mk_status(pid, session_id=SID, name="My Name", status="idle", cwd="/home"):
    return AgentStatus(pid=pid, session_id=session_id, name=name, name_source="user",
                     status=status, status_updated_ms=0, waiting_for=None, cwd=cwd)


class PaneBox:
    def __init__(self, session, role="agent", pid=None):
        self.session = session
        self.role = role
        self.pid = pid


class FakeClock:
    """Mutated by sleep(); readable by now() so waits terminate fast and deterministically."""
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeCockpit:
    def __init__(self, tmp):
        home = tmp / "home"
        home.mkdir()
        claude = tmp / "claude"
        claude.mkdir()
        state = tmp / "state"
        state.mkdir()
        self.paths = SimpleNamespace(home=home, claude_dir=claude, state=state,
                                  registry=home / "registry.json")
        self.panes_list = []
        self.tmux = SimpleNamespace(panes=lambda: self.panes_list)
        self.created = []

    def create(self, entry):
        self.created.append(entry)
        return None


def write_status(claude_dir, pid, session_id=SID, name="My Name", status="idle", cwd="/home/proj"):
    d = claude_dir / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps({
        "sessionId": session_id, "name": name, "status": status, "cwd": cwd,
    }))


def live_session(claude_dir, name="My Name", status="idle", session_id=SID, cwd="/home/proj"):
    """A real short-lived sleep with a matching status file (never a real Claude)."""
    child = subprocess.Popen(["sleep", "1000"])
    write_status(claude_dir, child.pid, session_id=session_id, name=name, status=status, cwd=cwd)
    return child


def write_transcript(claude_dir, session_id, cwd="/home/proj", title=None, user=True, project="someproj",
                     prompt=None):
    """A transcript; its user message is a slash command unless `prompt` is given (a typed first prompt names
    an untitled session; without one the name falls back to the folder)."""
    d = claude_dir / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "summary", "cwd": cwd})]
    if user:
        content = prompt if prompt is not None else "<command-name>/model</command-name>"
        lines.append(json.dumps({"type": "user", "message": {"role": "user", "content": content}}))
    if title:
        lines.append(json.dumps({"type": "custom-title", "customTitle": title}))
    (d / f"{session_id}.jsonl").write_text("\n".join(lines) + "\n")


def reg_entry(name, session_id="other-id"):
    return Entry(name=name, session_id=session_id, cwd="/home", created_at="now", order=1)


def save_entry(path, entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    save(path, Registry([entry]))


def patch_screen(monkeypatch, out, cmap, claude_pids):
    def fake_run(args, **kw):
        return SimpleNamespace(stdout=out)
    monkeypatch.setattr("force.importer.subprocess.run", fake_run)
    monkeypatch.setattr("force.importer.children_map", lambda proc: cmap)
    monkeypatch.setattr("force.importer.is_claude_pid",
                      lambda p, proc: p in claude_pids)


# --- parse_screen_ls --------------------------------------------------------
def test_parse_screen_ls_variants():
    text = (
        "There is a screen on:\n"
        "\t1474794.webshop\t(10/07/2026 09:29:26 AM)\t(Attached)\n"
        "\t123.a.b\t(10/07/2026 09:28:00 AM)\t(Detached)\n"
        "\t88.x\t(10/07/2026 09:27:00 AM)\t(Attached)\n"
        "1 Socket in /run/screen/S-alice.\n"
    )
    assert parse_screen_ls(text) == [(1474794, "webshop"), (123, "a.b"), (88, "x")]


def test_parse_screen_ls_multiple_dots():
    assert parse_screen_ls("\t12.a.b.c\t(date)\t(Detached)\n") == [(12, "a.b.c")]


def test_parse_screen_ls_no_sockets():
    assert parse_screen_ls("No Sockets found in /run/screen/S-alice.") == []
    assert parse_screen_ls("") == []


# --- /proc helpers ------------------------------------------------------------
def _write_stat(proc, pid, ppid, comm):
    p = proc / str(pid)
    p.mkdir(parents=True)
    (p / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0")


def test_children_map_tricky_comm(tmp_path):
    proc = tmp_path / "proc"
    _write_stat(proc, 100, 1, "(bash)")
    _write_stat(proc, 101, 100, "(a) b)")
    _write_stat(proc, 102, 101, "x) y) z)")
    _write_stat(proc, 300, 1, "weird) comm)")
    assert children_map(proc) == {1: [100, 300], 100: [101], 101: [102]}


def test_children_map_skips_unreadable(tmp_path):
    proc = tmp_path / "proc"
    (proc / "100").mkdir(parents=True)   # stat unreadable/failed -> ignored
    _write_stat(proc, 200, 1, "ok")
    assert children_map(proc) == {1: [200]}


def test_descendants_bfs():
    cmap = {1: [100, 300], 100: [101], 101: [102], 300: []}
    assert descendants(100, cmap) == [101, 102]
    # excludes the root itself
    assert 100 not in descendants(100, cmap)
    assert descendants(1, cmap) == [100, 300, 101, 102]
    assert descendants(999, cmap) == []


def test_ancestors(tmp_path):
    proc = tmp_path / "proc"
    _write_stat(proc, 100, 1, "(a)")
    _write_stat(proc, 101, 100, "(b)")
    _write_stat(proc, 102, 101, "(c)")
    assert ancestors(102, proc) == {1, 100, 101}
    assert ancestors(100, proc) == {1}
    assert ancestors(1, proc) == set()
    assert ancestors(99999, proc) == set()   # unreadable -> stop


# --- find_candidates: naming ---------------------------------------------------
def test_find_candidates_claude_name_slug(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    patch_screen(monkeypatch, SCREEN_OUT, {111111: [50001]}, {50001})
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(p, session_id="s-1", name="My Portal"))
    cands = find_candidates(cockpit)
    assert len(cands) == 1
    c = cands[0]
    assert (c.pid, c.screen_pid, c.screen_name) == (50001, 111111, "webshop")
    assert c.session_id == "s-1"
    assert c.target_name == "my-portal"
    assert c.skip_reason is None


def test_find_candidates_screen_fallback(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    patch_screen(monkeypatch, SCREEN_OUT, {111111: [50001]}, {50001})
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(p, session_id="s-1", name=None))
    cands = find_candidates(cockpit)
    assert cands[0].target_name == "webshop"
    assert cands[0].skip_reason is None


def test_find_candidates_slug_suffix(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    save_entry(cockpit.paths.registry, reg_entry("my-portal", session_id="zzz"))
    patch_screen(monkeypatch, SCREEN_OUT, {111111: [50001]}, {50001})
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(p, session_id="s-1", name="My Portal"))
    cands = find_candidates(cockpit)
    assert cands[0].target_name == "my-portal-2"
    assert cands[0].skip_reason is None


def test_find_candidates_unique_names_in_run(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    out = "\t111111.a\t(date)\n\t222222.b\t(date)\n"
    patch_screen(monkeypatch, out, {111111: [50001], 222222: [50002]}, {50001, 50002})
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(p, session_id=f"s-{p}", name="Same Name"))
    cands = find_candidates(cockpit)
    assert [c.target_name for c in cands] == ["same-name", "same-name-2"]


def test_find_candidates_explicit_name_taken(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    save_entry(cockpit.paths.registry, reg_entry("portal"))
    patch_screen(monkeypatch, SCREEN_OUT, {111111: [50001]}, {50001})
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(p, session_id="s-1", name="Whatever"))
    cands = find_candidates(cockpit, name="portal")
    assert cands[0].target_name is None
    assert cands[0].skip_reason == "name 'portal' invalid or taken"


def test_find_candidates_explicit_name_invalid_or_taken_pid(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    monkeypatch.setattr("force.importer.is_claude_pid", lambda p, proc: True)
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(12345, session_id="s-1", name="X", cwd="/h"))
    cands = find_candidates(cockpit, pid=12345, name="Bad Name!")
    assert cands[0].skip_reason == "name 'Bad Name!' invalid or taken"


# --- find_candidates: skips ---------------------------------------------------
def test_find_candidates_busy(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    patch_screen(monkeypatch, SCREEN_OUT, {111111: [50001]}, {50001})
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(p, session_id="s-1", name="My", status="busy"))
    cands = find_candidates(cockpit)
    assert cands[0].skip_reason == "busy (busy), retry later"


def test_find_candidates_no_status_file(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    patch_screen(monkeypatch, SCREEN_OUT, {111111: [50001]}, {50001})
    monkeypatch.setattr("force.importer.read_status", lambda d, p: None)
    cands = find_candidates(cockpit)
    assert len(cands) == 1
    assert cands[0].skip_reason == "no status file"


def test_find_candidates_already_managed_not_listed(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    cockpit.panes_list = [PaneBox("webshop", "agent", 50001)]
    patch_screen(monkeypatch, SCREEN_OUT, {111111: [50001]}, {50001})
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(p, session_id="s-1", name="My"))
    assert find_candidates(cockpit) == []


def test_find_candidates_self_skip(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    me = os.getpid()
    monkeypatch.setattr("force.importer.is_claude_pid", lambda p, proc: p == me)
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(me, session_id="s-1", name="My"))
    cands = find_candidates(cockpit, pid=me)
    assert cands[0].skip_reason == "this is the session running force import"


def test_find_candidates_ancestor_skip(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    parent = os.getppid()
    monkeypatch.setattr("force.importer.is_claude_pid", lambda p, proc: p == parent)
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(parent, session_id="s-1", name="My"))
    cands = find_candidates(cockpit, pid=parent)
    assert cands[0].skip_reason == "this is the session running force import"


def test_find_candidates_already_managed_session_id(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    save_entry(cockpit.paths.registry, reg_entry("portal", session_id="s-1"))
    monkeypatch.setattr("force.importer.is_claude_pid", lambda p, proc: True)
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(50001, session_id="s-1", name="My"))
    cands = find_candidates(cockpit, pid=50001)
    assert cands[0].skip_reason == "already managed as portal"


def test_find_candidates_not_a_claude_pid(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    monkeypatch.setattr("force.importer.is_claude_pid", lambda p, proc: False)
    cands = find_candidates(cockpit, pid=50001)
    assert len(cands) == 1
    assert cands[0].skip_reason == "not a claude process"


def test_find_candidates_screen_without_claude_skipped(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    patch_screen(monkeypatch, SCREEN_OUT, {111111: [777]}, set())
    monkeypatch.setattr("force.importer.read_status",
                     lambda d, p: mk_status(777, session_id="s-1", name="My"))
    assert find_candidates(cockpit) == []


def test_find_candidates_screen_missing_binary(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    def boom(args, **kw):
        raise FileNotFoundError
    monkeypatch.setattr("force.importer.subprocess.run", boom)
    assert find_candidates(cockpit) == []


def test_find_candidates_no_sockets(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    monkeypatch.setattr("force.importer.subprocess.run",
                     lambda args, **kw: SimpleNamespace(stdout="No Sockets found in ...\n"))
    assert find_candidates(cockpit) == []


# --- run_import --------------------------------------------------------------
def test_run_import_dry_run(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    out = "\t111111.webshop\t(date)\n\t222222.other\t(date)\n"
    patch_screen(monkeypatch, out, {111111: [50001], 222222: [50002]}, {50001, 50002})
    def rs(d, p):
        return mk_status(p, session_id=f"s-{p}", name="My Name",
                       status="idle" if p == 50001 else "busy")
    monkeypatch.setattr("force.importer.read_status", rs)
    lines = []
    rc = run_import(cockpit, dry_run=True, out=lines.append)
    assert rc == 0
    assert "would import 50001 (webshop) as my-name [s-50001]" in lines
    assert "skip 50002: busy (busy), retry later" in lines
    assert cockpit.created == []


def test_run_import_real(monkeypatch, tmp_path):
    child = subprocess.Popen(["sleep", "1000"])
    try:
        pid = child.pid
        cockpit = FakeCockpit(tmp_path)
        cockpit.panes_list = [PaneBox("portal", "agent", 777777)]
        monkeypatch.setattr("force.importer.is_claude_pid", lambda p, proc: p == pid)
        monkeypatch.setattr("force.importer.read_status",
                         lambda d, p: mk_status(p, session_id=SID, name="My Name"))
        clock = FakeClock()
        lines = []
        rc = run_import(cockpit, pid=pid, name="portal", out=lines.append,
                        sleep=clock.sleep, clock=clock.now)
        assert rc == 0
        assert lines == ["imported portal"]
        # registry entry created and create() called
        assert len(cockpit.created) == 1
        reg = load(cockpit.paths.registry)
        e = reg.get("portal")
        assert e is not None
        assert e.session_id == SID
        assert e.cwd == "/home"
        # the dummy claude process was terminated
        child.wait(timeout=5)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


def test_run_import_real_with_screen_quit(monkeypatch, tmp_path):
    child = subprocess.Popen(["sleep", "1000"])
    try:
        pid = child.pid
        cockpit = FakeCockpit(tmp_path)
        cockpit.panes_list = [PaneBox("my", "agent", 888888)]
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            if args == ["screen", "-ls"]:
                return SimpleNamespace(stdout="\t333333.webshop\t(date)\n")
            return SimpleNamespace(stdout="")

        monkeypatch.setattr("force.importer.subprocess.run", fake_run)
        monkeypatch.setattr("force.importer.children_map", lambda proc: {333333: [pid]})
        monkeypatch.setattr("force.importer.is_claude_pid", lambda p, proc: p == pid)
        monkeypatch.setattr("force.importer.read_status",
                         lambda d, p: mk_status(p, session_id=SID, name="My Name"))
        clock = FakeClock()
        lines = []
        rc = run_import(cockpit, name="my", out=lines.append, sleep=clock.sleep, clock=clock.now)
        assert rc == 0
        assert lines == ["imported my (was screen webshop)"]
        assert ["screen", "-S", "333333", "-X", "quit"] in calls
        child.wait(timeout=5)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


def test_run_import_failure_keeps_registry(monkeypatch, tmp_path):
    child = subprocess.Popen(["sleep", "1000"])
    try:
        pid = child.pid
        cockpit = FakeCockpit(tmp_path)
        # no claude pane ever appears -> wait fails
        cockpit.panes_list = []
        monkeypatch.setattr("force.importer.is_claude_pid", lambda p, proc: p == pid)
        monkeypatch.setattr("force.importer.read_status",
                         lambda d, p: mk_status(p, session_id=SID, name="My Name"))
        clock = FakeClock()
        lines = []
        rc = run_import(cockpit, pid=pid, name="portal", out=lines.append,
                        sleep=clock.sleep, clock=clock.now)
        assert rc == 1
        assert lines and lines[0].startswith("failed portal: ")
        # registry entry is kept so the daemon can restore it
        reg = load(cockpit.paths.registry)
        assert reg.get("portal") is not None
        child.wait(timeout=5)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


# --- run_list (no arguments = list) -------------------------------------------
def test_run_list_nothing(tmp_path):
    cockpit = FakeCockpit(tmp_path)
    lines = []
    run_list(cockpit, out=lines.append)
    assert lines == ["force: nothing to import"]


def test_run_list_groups(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    child = live_session(cockpit.paths.claude_dir, name="my-session-name", status="idle",
                        session_id="0ff9a485000000000000000000000000", cwd="/home/proj")
    try:
        proj = tmp_path / "other"; proj.mkdir()
        write_transcript(cockpit.paths.claude_dir, "7c1e22aa-0000-0000-0000-000000000000",
                        cwd=str(proj), title="title-or-summary", project="p1")
        lines = []
        run_list(cockpit, out=lines.append, now=10_000_000)
        text = "\n".join(lines)
        assert "running (not in Force):" in text
        assert "stopped (newest first):" in text
        assert "0ff9a485" in text and "idle" in text and "my-session-name" in text
        assert "force import 0ff9a485" in text
        assert "7c1e22aa" in text and "title-or-summary" in text
        assert "force import 7c1e22aa" in text
    finally:
        child.kill()


def test_run_list_home_tilde(monkeypatch, tmp_path):
    fakehome = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fakehome))
    cockpit = FakeCockpit(tmp_path)
    proj = fakehome / "proj"; proj.mkdir(parents=True)
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(proj))
    lines = []
    run_list(cockpit, out=lines.append)
    assert any("~" in l and str(proj) not in l for l in lines)
    assert any("~/proj" in l for l in lines)


def test_run_list_stopped_names(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "alpha" / "beta"; proj.mkdir(parents=True)
    write_transcript(cockpit.paths.claude_dir, "11111111-0000-0000-0000-000000000000",
                    cwd=str(proj), title="Hello World", project="p1")
    write_transcript(cockpit.paths.claude_dir, "22222222-0000-0000-0000-000000000000",
                    cwd=str(proj), project="p2")
    lines = []
    run_list(cockpit, out=lines.append)
    text = "\n".join(lines)
    assert "Hello World" in text          # customTitle shown raw
    assert "beta" in text                 # cwd basename fallback
    assert f"force import 22222222" in text


def test_run_list_exclusions(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    save(cockpit.paths.registry, Registry([
        reg_entry("managed", session_id="aaaaaaaa-0000-0000-0000-000000000000"),
        reg_entry("stopped", session_id="dddddddd-0000-0000-0000-000000000000"),
    ]))
    managed = live_session(cockpit.paths.claude_dir, name="managed",
                          session_id="aaaaaaaa-0000-0000-0000-000000000000")
    try:
        agent = live_session(cockpit.paths.claude_dir, name="agentpane",
                            session_id="bbbbbbbb-0000-0000-0000-000000000000")
        try:
            cockpit.panes_list = [PaneBox("x", "agent", agent.pid)]
            smoke = cockpit.paths.state / "smoke"; (smoke / "work").mkdir(parents=True)
            write_transcript(cockpit.paths.claude_dir, "abababab-0000-0000-0000-000000000000",
                            cwd=str(smoke / "work"), project="s")
            write_transcript(cockpit.paths.claude_dir, "dddddddd-0000-0000-0000-000000000000",
                            cwd=str(tmp_path), project="r")
            # a transcript with no user message
            write_transcript(cockpit.paths.claude_dir, "eeeeeeee-0000-0000-0000-000000000000",
                            cwd=str(tmp_path), user=False, project="n")
            lines = []
            run_list(cockpit, out=lines.append)
            text = "\n".join(lines)
            assert "aaaaaaaa" not in text      # in registry
            assert "bbbbbbbb" not in text      # force agent pane
            assert "abababab" not in text      # under <FORCE_STATE>/smoke
            assert "dddddddd" not in text      # stopped but in registry
            assert "eeeeeeee" not in text      # no user message
        finally:
            agent.kill()
    finally:
        managed.kill()


def test_run_list_cap_and_all(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "proj"; proj.mkdir()
    sids = [f"aaaa{i:04d}-0000-0000-0000-000000000000" for i in range(20)]
    for i, sid in enumerate(sids):
        write_transcript(cockpit.paths.claude_dir, sid, cwd=str(proj), project=f"p{i}")
    lines = []
    run_list(cockpit, out=lines.append)
    n_default = sum(1 for l in lines if l.startswith("  ") and "force import" in l)
    assert n_default == 15
    lines2 = []
    run_list(cockpit, out=lines2.append, all_=True)
    n_all = sum(1 for l in lines2 if l.startswith("  ") and "force import" in l)
    assert n_all == 20


# --- prefix / session resolution ----------------------------------------------
def test_import_prefix_unique(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    write_transcript(cockpit.paths.claude_dir, "abcdef12-0000-0000-0000-000000000000", cwd=str(proj))
    monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
    lines = []
    rc = import_session(cockpit, "abcdef", out=lines.append)
    assert rc == 0
    reg = load(cockpit.paths.registry)
    assert reg.get("p") is not None


def test_import_full_session_id(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(proj))
    monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
    rc = import_session(cockpit, SID)
    assert rc == 0
    assert load(cockpit.paths.registry).get("p") is not None


def test_import_prefix_ambiguous(tmp_path, capsys):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    write_transcript(cockpit.paths.claude_dir, "abcdef12-0000-0000-0000-000000000000", cwd=str(proj), project="p1")
    write_transcript(cockpit.paths.claude_dir, "abcdef34-0000-0000-0000-000000000000", cwd=str(proj), project="p2")
    rc = import_session(cockpit, "abcdef")
    assert rc == 1
    assert "ambiguous" in capsys.readouterr().err


def test_import_prefix_too_short(tmp_path, capsys):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    write_transcript(cockpit.paths.claude_dir, "abcdef12-0000-0000-0000-000000000000", cwd=str(proj))
    rc = import_session(cockpit, "abcde")
    assert rc == 1
    assert "at least 6" in capsys.readouterr().err


def test_import_no_match(tmp_path, capsys):
    cockpit = FakeCockpit(tmp_path)
    rc = import_session(cockpit, "zzzzzz")
    assert rc == 1
    assert "no Claude session matching" in capsys.readouterr().err


# --- stopped import -----------------------------------------------------------
def test_import_stopped(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "work" / "proj"; proj.mkdir(parents=True)
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(proj))
    monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
    lines = []
    rc = import_session(cockpit, SID, out=lines.append)
    assert rc == 0
    assert lines == [f"imported proj ({SID[:8]}): open it with force proj"]
    reg = load(cockpit.paths.registry)
    e = reg.get("proj")
    assert e is not None and e.session_id == SID and e.cwd == str(proj)
    assert len(cockpit.created) == 1


def test_import_stopped_title_name(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(proj), title="Hello World")
    monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
    lines = []
    rc = import_session(cockpit, SID, out=lines.append)
    assert rc == 0
    assert lines == [f"imported hello-world ({SID[:8]}): open it with force hello-world"]
    assert load(cockpit.paths.registry).get("hello-world") is not None


def test_import_stopped_missing_folder(tmp_path, capsys):
    cockpit = FakeCockpit(tmp_path)
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(tmp_path / "gone"))
    rc = import_session(cockpit, SID)
    assert rc == 1
    err = capsys.readouterr().err
    assert f"its folder {tmp_path / 'gone'} no longer exists" in err


def test_import_dry_run(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(proj))
    lines = []
    rc = import_session(cockpit, SID, dry_run=True, out=lines.append)
    assert rc == 0
    assert lines == [f"would import {SID[:8]} as p (stopped, {str(proj)})"]
    assert load(cockpit.paths.registry).entries == []
    assert cockpit.created == []


def test_import_already_managed(tmp_path, capsys):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(proj))
    save_entry(cockpit.paths.registry, reg_entry("managed", session_id=SID))
    rc = import_session(cockpit, SID)
    assert rc == 1
    assert "already in Force as managed" in capsys.readouterr().err


# --- running import -----------------------------------------------------------
def test_import_running(monkeypatch, tmp_path):
    child = subprocess.Popen(["sleep", "1000"])
    try:
        pid = child.pid
        cockpit = FakeCockpit(tmp_path)
        write_status(cockpit.paths.claude_dir, pid, session_id=SID, name="My Session", cwd="/home/proj")
        monkeypatch.setattr("force.importer._screen_for_pid", lambda pid, proc=None: (None, None))
        monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
        clock = FakeClock()
        lines = []
        rc = import_session(cockpit, str(pid), out=lines.append, sleep=clock.sleep, clock=clock.now)
        assert rc == 0
        assert lines == [f"imported my-session ({SID[:8]}): open it with force my-session"]
        reg = load(cockpit.paths.registry)
        e = reg.get("my-session")
        assert e is not None and e.session_id == SID and e.cwd == "/home/proj"
        assert len(cockpit.created) == 1
        child.wait(timeout=5)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


def test_import_running_dry_run(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    child = live_session(cockpit.paths.claude_dir, name="My Session", cwd="/home/proj")
    try:
        monkeypatch.setattr("force.importer._screen_for_pid", lambda pid, proc=None: (None, None))
        lines = []
        rc = import_session(cockpit, str(child.pid), dry_run=True, out=lines.append)
        assert rc == 0
        assert lines == [f"would import {SID[:8]} as my-session (running, /home/proj)"]
        assert load(cockpit.paths.registry).entries == []
        assert cockpit.created == []
        assert child.poll() is None
    finally:
        child.kill()


def test_import_running_by_name(monkeypatch, tmp_path):
    child = subprocess.Popen(["sleep", "1000"])
    try:
        pid = child.pid
        cockpit = FakeCockpit(tmp_path)
        write_status(cockpit.paths.claude_dir, pid, session_id=SID, name="my-session", cwd="/home/proj")
        monkeypatch.setattr("force.importer._screen_for_pid", lambda pid, proc=None: (None, None))
        monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
        rc = import_session(cockpit, "my-session")
        assert rc == 0
        assert load(cockpit.paths.registry).get("my-session") is not None
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()


def test_import_running_busy(tmp_path, capsys):
    cockpit = FakeCockpit(tmp_path)
    child = live_session(cockpit.paths.claude_dir, name="My Session", status="busy", cwd="/home/proj")
    try:
        rc = import_session(cockpit, str(child.pid))
        assert rc == 1
        err = capsys.readouterr().err
        assert "is busy (busy); import it when it is idle" in err
        assert load(cockpit.paths.registry).entries == []
    finally:
        child.kill()


# --- name chain: running falls back to transcript title / cwd basename ------------
def test_import_running_uses_custom_title(monkeypatch, tmp_path):
    child = subprocess.Popen(["sleep", "1000"])
    try:
        pid = child.pid
        cockpit = FakeCockpit(tmp_path)
        write_status(cockpit.paths.claude_dir, pid, session_id=SID, name=None, cwd="/home/proj")
        # a transcript with a customTitle but no status name
        write_transcript(cockpit.paths.claude_dir, SID, cwd="/home/proj", title="Hello World")
        monkeypatch.setattr("force.importer._screen_for_pid", lambda pid, proc=None: (None, None))
        monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
        clock = FakeClock()
        rc = import_session(cockpit, str(pid), sleep=clock.sleep, clock=clock.now)
        assert rc == 0
        e = load(cockpit.paths.registry).get("hello-world")
        assert e is not None and e.session_id == SID
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()


def test_import_running_uses_cwd_basename(monkeypatch, tmp_path):
    child = subprocess.Popen(["sleep", "1000"])
    try:
        pid = child.pid
        cockpit = FakeCockpit(tmp_path)
        base = tmp_path / "myproj"; base.mkdir()
        write_status(cockpit.paths.claude_dir, pid, session_id=SID, name=None, cwd=str(base))
        # no transcript at all -> chain falls to the cwd basename
        monkeypatch.setattr("force.importer._screen_for_pid", lambda pid, proc=None: (None, None))
        monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
        clock = FakeClock()
        rc = import_session(cockpit, str(pid), sleep=clock.sleep, clock=clock.now)
        assert rc == 0
        e = load(cockpit.paths.registry).get("myproj")
        assert e is not None and e.session_id == SID
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()


def test_import_stopped_symbol_title_uses_cwd_basename(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "myproj"; proj.mkdir()
    # a title that slugifies to None (only symbols) must fall back to the cwd basename
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(proj), title="!!! ???")
    monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
    rc = import_session(cockpit, SID)
    assert rc == 0
    e = load(cockpit.paths.registry).get("myproj")
    assert e is not None and e.session_id == SID and e.cwd == str(proj)


def test_non_interactive_runs_are_not_offered(tmp_path):
    """`claude -p` / SDK runs (entrypoint sdk-cli, e.g. delegated jobs) are not sessions anyone reopens."""
    from force import importer
    sdk = tmp_path / "a.jsonl"
    sdk.write_text('{"type":"queue-operation"}\n{"type":"user","entrypoint":"sdk-cli","cwd":"/x"}\n')
    cli = tmp_path / "b.jsonl"
    cli.write_text('{"type":"user","entrypoint":"cli","cwd":"/x"}\n')
    old = tmp_path / "c.jsonl"
    old.write_text('{"type":"user","cwd":"/x"}\n')
    assert importer._has_user_message(sdk) is False
    assert importer._has_user_message(cli) is True
    assert importer._has_user_message(old) is True


def test_an_untitled_session_is_named_after_its_first_prompt(tmp_path):
    from force import importer
    t = tmp_path / "x.jsonl"
    t.write_text("\n".join([
        '{"type":"user","isMeta":true,"message":{"content":"<local-command-caveat>Caveat: ..."}}',
        '{"type":"user","message":{"content":"<command-name>/clear</command-name>"}}',
        '{"type":"user","message":{"content":[{"type":"text","text":"i have a wired  ethernet usb adapter plugged in"}]}}',
    ]) + "\n")
    assert importer._first_prompt(t) == "i have a wired ethernet"
    from force.registry import slugify
    assert slugify(importer._first_prompt(t)) == "i-have-a-wired-ethernet"


def test_import_stopped_untitled_is_named_after_its_first_prompt(monkeypatch, tmp_path):
    cockpit = FakeCockpit(tmp_path)
    proj = tmp_path / "alice"; proj.mkdir()
    write_transcript(cockpit.paths.claude_dir, SID, cwd=str(proj), prompt="set up a DHCP server on eth1 please")
    monkeypatch.setattr("force.importer._wait_for_claude", lambda *a, **k: True)
    lines = []
    assert import_session(cockpit, SID, out=lines.append) == 0
    assert lines == [f"imported set-up-a-dhcp-server ({SID[:8]}): open it with force set-up-a-dhcp-server"]
