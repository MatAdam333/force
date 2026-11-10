"""The agent guide (SPEC §49.5): it prints, and every command it shows exists."""
import re
import subprocess
import sys
from pathlib import Path

from force import cli

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "share" / "skill.md"


def _subcommands() -> set[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            return set(action.choices)
    raise AssertionError("no subcommands")


def test_every_command_in_the_guide_exists():
    subs = _subcommands()
    used = set(re.findall(r"^\s+force ([a-z][a-z-]*)", GUIDE.read_text(), re.M))
    assert used, "the guide shows commands"
    assert used <= subs, f"unknown commands in the guide: {sorted(used - subs)}"
    for must in ("prompt", "wait", "read", "keys", "status", "explain", "new", "settings", "notify-test"):
        assert must in used


def test_skill_prints_the_guide(capsys):
    assert cli.skill_command(None) == 0          # the dev tree's share/ is found next to the package
    out = capsys.readouterr().out
    assert out == GUIDE.read_text()


def test_dash_dash_skill(tmp_path):
    p = subprocess.run([sys.executable, "-m", "force.cli", "--skill"], capture_output=True, text=True,
                       cwd=str(ROOT), env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT), "HOME": str(tmp_path)})
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("# Force: a guide for agents")
