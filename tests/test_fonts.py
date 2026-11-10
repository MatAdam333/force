"""Force fonts (SPEC §45): share/fonts.sh in a fake HOME (no network, no DBus), and the cli commands."""
import subprocess
from pathlib import Path

import pytest

import force.cli as cli

SHARE = Path(__file__).resolve().parent.parent / "share"
SCRIPT = SHARE / "fonts.sh"


def _sandbox(tmp_path):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    runtime = tmp_path / "no-runtime"
    runtime.mkdir()
    # XDG_RUNTIME_DIR has no bus socket and no DBUS_SESSION_BUS_ADDRESS -> dconf/gsettings are skipped
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "XDG_CONFIG_HOME": str(home / ".config"),
           "XDG_RUNTIME_DIR": str(runtime)}
    return home, env


def test_fonts_script_parses():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("arg", ["-h", "--help"])
def test_fonts_help(tmp_path, arg):
    home, env = _sandbox(tmp_path)
    r = subprocess.run(["bash", str(SCRIPT), arg], env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert "force fonts" in r.stdout


def test_fonts_unknown_argument_is_usage_2(tmp_path):
    home, env = _sandbox(tmp_path)
    r = subprocess.run(["bash", str(SCRIPT), "--host", "x"], env=env, capture_output=True, text=True)
    assert r.returncode == 2


def _plant_old_kit(home):
    apps = home / ".local/share/applications"
    apps.mkdir(parents=True)
    for t in ("kitty", "konsole"):
        (apps / f"force-{t}.desktop").write_text("[Desktop Entry]\n")
    (home / ".config/kitty").mkdir(parents=True)
    (home / ".config/kitty/force.conf").write_text("x")
    kd = home / ".local/share/konsole"
    kd.mkdir(parents=True)
    (kd / "Force.profile").write_text("x")
    (kd / "Force.colorscheme").write_text("x")
    (kd / "Force.keytab").write_text("x")
    return apps


def test_cleanup_only_removes_the_old_kit(tmp_path):
    home, env = _sandbox(tmp_path)
    apps = _plant_old_kit(home)
    (apps / "other.desktop").write_text("keep")          # an unrelated launcher survives
    fonts = home / ".local/share/fonts/force"
    fonts.mkdir(parents=True)
    (fonts / "keep.ttf").write_text("keep")            # --cleanup-only never touches the fonts dir
    r = subprocess.run(["bash", str(SCRIPT), "--cleanup-only"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "removed the old terminal profiles and launchers" in r.stdout
    for t in ("kitty", "konsole"):
        assert not (apps / f"force-{t}.desktop").exists()
    assert not (home / ".config/kitty/force.conf").exists()
    for n in ("Force.profile", "Force.colorscheme", "Force.keytab"):
        assert not (home / ".local/share/konsole" / n).exists()
    assert (apps / "other.desktop").exists()
    assert (fonts / "keep.ttf").exists()


def test_cleanup_only_prints_nothing_when_there_is_nothing(tmp_path):
    home, env = _sandbox(tmp_path)
    r = subprocess.run(["bash", str(SCRIPT), "--cleanup-only"], env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert "removed" not in r.stdout


def test_uninstall_removes_the_font_dir(tmp_path):
    home, env = _sandbox(tmp_path)
    _plant_old_kit(home)
    fonts = home / ".local/share/fonts/force"
    fonts.mkdir(parents=True)
    (fonts / "SymbolsNerdFont-Regular.ttf").write_text("x")
    other = home / ".local/share/fonts/other"
    other.mkdir(parents=True)
    (other / "a.ttf").write_text("keep")               # other fonts survive
    r = subprocess.run(["bash", str(SCRIPT), "--uninstall"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not (home / ".local/share/applications/force-kitty.desktop").exists()
    assert not fonts.exists()
    assert (other / "a.ttf").exists()


@pytest.mark.parametrize("args,want", [
    ([], []),
    (["--cleanup-only"], ["--cleanup-only"]),
    (["--uninstall"], ["--uninstall"]),
])
def test_fonts_command_execs_fonts_sh_with_args(monkeypatch, args, want):
    captured = {}
    monkeypatch.setattr(cli.os, "execvp", lambda *a: captured.setdefault("argv", a[1]))
    assert cli.fonts_command(args) == 0
    argv = captured["argv"]
    assert argv[0] == "bash"
    assert argv[1].endswith("share/fonts.sh")
    assert argv[2:] == want


@pytest.mark.parametrize("args", [["--uninstall"], ["--cleanup-only"], []])
def test_force_fonts_passes_every_argument_through(monkeypatch, args):
    """`force fonts --uninstall` must reach the script: argparse's REMAINDER drops a leading option."""
    captured = {}
    monkeypatch.setattr(cli.os, "execvp", lambda *a: captured.setdefault("argv", a[1]))
    assert cli.main(["fonts", *args]) == 0
    assert captured["argv"][2:] == args


@pytest.mark.parametrize("args,want", [
    ([], []),
    (["--no-fonts"], ["--cleanup-only"]),
    (["--uninstall"], ["--uninstall"]),
])
def test_terminals_alias_runs_fonts_cleanup(monkeypatch, args, want, capsys):
    captured = {}
    monkeypatch.setattr(cli.os, "execvp", lambda *a: captured.setdefault("argv", a[1]))
    assert cli.main(["terminals", *args]) == 0
    assert "terminal profiles are gone, Force themes itself now" in capsys.readouterr().err
    argv = captured["argv"]
    assert argv[0] == "bash"
    assert argv[1].endswith("share/fonts.sh")
    assert argv[2:] == want
