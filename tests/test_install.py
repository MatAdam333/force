"""The developer install (install.sh: venv, deploy.sh, then force.setup) and deploy.sh's python selection.
The one-line user installer is tested in tests/test_setup.py."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"
DEPLOY = REPO / "deploy.sh"


def _env(home: Path, extra=None, path=None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("FORCE_PY", None)
    if path is not None:
        env["PATH"] = path
    if extra:
        env.update(extra)
    return env


def test_scripts_parse():
    for script in (INSTALL, DEPLOY):
        r = subprocess.run(["bash", "-n", str(script)], capture_output=True)
        assert r.returncode == 0, r.stderr


def _only(tmp_path, tools):
    bind = tmp_path / "bin"
    bind.mkdir()
    for tool in tools:
        real = shutil.which(tool)
        if real:
            (bind / tool).symlink_to(real)
    return str(bind)


def test_install_needs_python_311(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run([str(INSTALL)], cwd=REPO, env=_env(home, path=_only(tmp_path, ("bash", "env", "dirname"))),
                       capture_output=True, text=True)
    assert r.returncode == 1 and "Python 3.11+" in r.stderr
    assert list(home.rglob("*")) == [], "nothing written"


def test_install_reports_missing_tmux(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run([str(INSTALL)], cwd=REPO,
                       env=_env(home, path=_only(tmp_path, ("bash", "env", "dirname", "python3"))),
                       capture_output=True, text=True)
    assert r.returncode == 1 and "tmux" in r.stderr
    assert list(home.rglob("*")) == []


def test_deploy_print_python_force_py_overrides(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run([str(DEPLOY), "--print-python"], cwd=REPO,
                       env=_env(home, extra={"FORCE_PY": "/tmp/fake/python"}),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/tmp/fake/python"


def test_deploy_print_python_uses_install_venv(tmp_path):
    # the force venv is preferred
    home = tmp_path / "home"
    py = home / ".local" / "share" / "force" / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    r = subprocess.run([str(DEPLOY), "--print-python"], cwd=REPO, env=_env(home),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(py)


def test_deploy_print_python_none_found(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run([str(DEPLOY), "--print-python"], cwd=REPO, env=_env(home),
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "install.sh" in r.stderr
