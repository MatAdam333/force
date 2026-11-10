"""Every shell script in share/ must at least parse (a quoting slip breaks a menu silently)."""
import subprocess
from pathlib import Path

import pytest

SHARE = Path(__file__).resolve().parent.parent / "share"
SYSTEMD = Path(__file__).resolve().parent.parent / "systemd"


@pytest.mark.parametrize("script", sorted(SHARE.glob("*.sh")), ids=lambda p: p.name)
def test_script_parses(script):
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_systemd_units_exist():
    """The four units exist; force-tmux.service runs the host tmux server on socket `force`."""
    for name in ("force-tmux.service", "force.service", "force-smoke.service", "force-smoke.timer"):
        assert (SYSTEMD / name).exists(), f"missing systemd unit {name}"
    tmux = (SYSTEMD / "force-tmux.service").read_text()
    assert "tmux -L force" in tmux


def test_tier_hook_mode_never_fails(tmp_path):
    """tmux shows a failing run-shell hook's status over the window, so hook mode must exit 0 even when
    nothing changes or the server is gone."""
    sock = tmp_path / "no-such-socket"
    r = subprocess.run(["sh", str(SHARE / "tier.sh"), str(sock), "cockpit", "--signal"], capture_output=True)
    assert r.returncode == 0
    r = subprocess.run(["sh", str(SHARE / "tier.sh"), str(sock), "cockpit"], capture_output=True)
    assert r.returncode == 1, "without --signal: 1 = no change (switched.sh relies on it)"


def _fake_tmux(tmp_path, clients, rich="", stated=""):
    """A `tmux` stub for tier.sh: list-clients prints `clients`, `show -gqv @force_rich_terms` prints `rich`,
    the session's @force_glyphs starts unset and set-option records the new tier in tmp_path/tier."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "clients").write_text("".join(f"s1\t/dev/pts/{i}\t{c}\n" for i, c in enumerate(clients)))
    stub = bindir / "tmux"
    stub.write_text(f"""#!/bin/sh
shift 2
case "$1" in
  list-clients) cat {tmp_path}/clients ;;
  show) case "$*" in *-g*@force_rich_terms*) printf '%s\\n' '{rich}' ;;
               *-g*@force_client_glyphs*) printf '%s\\n' '{stated}' ;;
               *) cat {tmp_path}/tier 2>/dev/null ;; esac ;;
  set-option) printf '%s' "$5" > {tmp_path}/tier ;;
  list-panes) ;;
esac
""")
    stub.chmod(0o755)
    return bindir


@pytest.mark.parametrize("clients,rich,want", [
    (["xterm-kitty kitty(0.49.2)"], "", "rich"),
    (["xterm-256color VTE(7600)"], "", "plain"),                       # Tilix: plain by default
    (["xterm-256color VTE(7600)"], "kitty|ghostty|wezterm|vte", "rich"),  # ... rich once opted in
    (["xterm-kitty kitty(0.49.2)", "xterm-256color VTE(7600)"], "kitty", "plain"),  # mixed viewers
    (["xterm-kitty kitty(0.49.2)", "xterm-256color VTE(7600)"], "kitty|vte", "rich"),
])
def test_tier_follows_rich_terminals(tmp_path, clients, rich, want):
    _run_tier(tmp_path, clients, rich, "", want)


@pytest.mark.parametrize("clients,stated,want", [
    (["xterm-256color Konsole"], "/dev/pts/0=rich", "rich"),            # stated by the client kit
    (["xterm-256color VTE(7600)"], "/dev/pts/7=rich", "plain"),           # another tty's statement
    (["xterm-kitty kitty(0.49.2)"], "/dev/pts/0=plain", "plain"),         # stated beats the type
    (["xterm-kitty kitty(0.49.2)", "xterm-256color VTE(7600)"], "/dev/pts/1=rich", "rich"),
])
def test_tier_follows_the_client_statement(tmp_path, clients, stated, want):
    _run_tier(tmp_path, clients, "kitty|ghostty|wezterm", stated, want)


def _run_tier(tmp_path, clients, rich, stated, want):
    import os
    bindir = _fake_tmux(tmp_path, clients, rich, stated)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    r = subprocess.run(["sh", str(SHARE / "tier.sh"), "/sock", "s1"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "tier").read_text() == want


def _settle_env(tmp_path, htmux):
    """A `tmux` stub for settle.sh: the client shows window @1 whose nested client is /dev/pts/7; every call is
    logged to tmp_path/log (the host's refresh-client too, through the same stub)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "tmux"
    stub.write_text(f"""#!/bin/sh
echo "$*" >> {tmp_path}/log
case "$*" in
  *window_id*) echo @1 ;;
  *@force_nested*) echo /dev/pts/7 ;;
  *@force_host_tmux*) echo '{htmux}' ;;
  *list-panes*) ;;
esac
""")
    stub.chmod(0o755)
    import os
    return dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", XDG_RUNTIME_DIR=str(tmp_path))


def test_settle_refreshes_the_nested_client_once_after_a_burst(tmp_path):
    """A drag fires client-resized many times: only the last run repaints (the session's nested client
    refreshed on its host), and it always exits 0 (a hook)."""
    env = _settle_env(tmp_path, "tmux -L forcetest-host")
    procs = [subprocess.Popen(["sh", str(SHARE / "settle.sh"), "/tmp/nope", "/dev/pts/3"], env=env)
             for _ in range(4)]
    assert all(p.wait(timeout=10) == 0 for p in procs)
    log = (tmp_path / "log").read_text().splitlines()
    assert [l for l in log if "refresh-client" in l] == ["-L forcetest-host refresh-client -t /dev/pts/7"]


def test_settle_without_a_server_is_quiet(tmp_path):
    r = subprocess.run(["sh", str(SHARE / "settle.sh"), str(tmp_path / "no-such-socket"), "/dev/pts/3"],
                       capture_output=True, env=dict(__import__("os").environ, XDG_RUNTIME_DIR=str(tmp_path)))
    assert r.returncode == 0
