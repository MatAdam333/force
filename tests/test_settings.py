"""settings.json / hosts.json and the `force settings` / `force host` commands (SPEC §2)."""
import json
import dataclasses
from pathlib import Path

import pytest

from force import cli, remote
from force.config import (Config, get_paths, load_config, parse_value, save_setting,
                       unset_setting, schema)


def test_schema_is_draft07_object():
    s = schema()
    assert s["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert s["properties"]["animations"]["enum"] == ["full", "subtle", "off"]
    assert s["properties"]["layout"]["default"] == "split"      # new installs get split
    assert s["properties"]["introCellAspect"]["type"] == "number"
    assert s["properties"]["sidebarWidth"]["type"] == "integer"


def test_schema_shipped_file_matches(tmp_path):
    import subprocess, sys
    out = subprocess.run([sys.executable, "-m", "force.config", "--schema"], capture_output=True,
                       text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert out.returncode == 0
    shipped = (Path(__file__).resolve().parent.parent / "share" / "settings.schema.json").read_text()
    assert out.stdout == shipped


# --- CLI: force settings --------------------------------------------------------
def _cli(env, monkeypatch, tmp_path):
    cockpit = env.make_cockpit()
    monkeypatch.setattr(cli, "make_cockpit", lambda: cockpit)
    return cockpit


def test_settings_prints_public_with_default_markers(env, monkeypatch, capsys):
    cockpit = _cli(env, monkeypatch, env.tmp)
    assert cli.main(["settings"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["animations", "full", "(default)"]
    assert ['opencodeManaged', 'false', '(default)'] in [l.split() for l in lines]
    # the fixture's settings.json sets layout, so it prints without a default marker
    assert ['layout', 'classic'] in [l.split() for l in lines] and "layout  classic  (default)" not in lines
    assert not any(l.startswith("crashRestartMax") for l in lines)      # advanced hidden


def test_settings_all_and_json(env, monkeypatch, capsys):
    cockpit = _cli(env, monkeypatch, env.tmp)
    assert cli.main(["settings", "--all"]) == 0
    out = capsys.readouterr().out
    assert ["crashRestartMax", "3", "(default)"] in [l.split() for l in out.splitlines()]
    assert ['opencodeManaged', 'false', '(default)'] in [l.split() for l in out.splitlines()]
    assert cli.main(["settings", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["layout"] == "classic" and data["icons"] == "auto" and data["opencodeManaged"] is False


def test_settings_set_unset(env, monkeypatch, capsys):
    cockpit = _cli(env, monkeypatch, env.tmp)
    assert cli.main(["settings", "set", "layout", "split"]) == 0
    out = capsys.readouterr().out
    assert "layout = split" in out and "daemon reloads" in out
    assert json.loads(env.paths.config_file.read_text())["layout"] == "split"
    capsys.readouterr()                              # drop the unset note
    assert cli.main(["settings", "unset", "layout"]) == 0
    assert "layout" not in json.loads(env.paths.config_file.read_text())
    capsys.readouterr()                              # drop the unset note
    assert cli.main(["settings", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(env.paths.config_file)


def test_settings_set_bad_key_and_bad_value_exit_1(env, monkeypatch, capsys):
    _cli(env, monkeypatch, env.tmp)
    assert cli.main(["settings", "set", "bogus", "1"]) == 1
    assert "unknown settings key" in capsys.readouterr().err
    assert cli.main(["settings", "set", "sidebarWidth", "abc"]) == 1
    assert "expected an int" in capsys.readouterr().err
    assert cli.main(["settings", "set", "animations", "fast"]) == 1
    assert "must be one of" in capsys.readouterr().err
    assert cli.main(["settings", "unset", "bogus"]) == 1


def test_settings_edit_creates_and_validates(env, monkeypatch, capsys):
    _cli(env, monkeypatch, env.tmp)
    env.paths.config_file.unlink()
    monkeypatch.setenv("VISUAL", "true")                 # a no-op editor
    assert cli.main(["settings", "edit"]) == 0
    assert env.paths.config_file.read_text() == "{}\n"
    out = capsys.readouterr().out
    assert "settings ok" in out and "daemon reloads" in out


# --- CLI: force host add NAME [SSH] (and the remote alias) ---------------------
def test_host_add_defaults_ssh_to_name(env, monkeypatch, capsys):
    cockpit = _cli(env, monkeypatch, env.tmp)

    class Done:
        def __init__(self, out):
            self.returncode, self.stdout, self.stderr = 0, out, ""
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: Done("split"))
    assert cli.main(["host", "add", "buildbox"]) == 0           # no SSH: defaults to NAME
    rs = remote.load(env.paths)
    assert [(r.name, r.ssh, r.enabled) for r in rs] == [("buildbox", "buildbox", True)]
    out = capsys.readouterr().out
    assert "no hosts (force host add NAME [SSH])" not in out   # added now
    assert cli.main(["host", "add", "buildbox", "buildbox"]) == 1  # exists
    assert "exists" in capsys.readouterr().err


def test_host_list_remote_alias(env, monkeypatch, capsys):
    _cli(env, monkeypatch, env.tmp)
    remote.save(env.paths, [remote.Remote("buildbox", "buildbox.example")])
    assert cli.main(["host", "list"]) == 0
    assert "buildbox  buildbox.example" in capsys.readouterr().out
    assert cli.main(["remote", "list"]) == 0                 # the alias
    assert "buildbox  buildbox.example" in capsys.readouterr().out


def test_host_list_empty_hint(env, monkeypatch, capsys):
    _cli(env, monkeypatch, env.tmp)
    assert cli.main(["host", "list"]) == 0
    assert "no hosts (force host add NAME [SSH])" in capsys.readouterr().out
