import http.server
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from force import updates

# The handler reads a module-global body so each test can set what install.sh returns.
_server_body = b""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(_server_body)))
        self.end_headers()
        self.wfile.write(_server_body)

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def set_body(body: bytes) -> None:
    global _server_body
    _server_body = body


@pytest.fixture
def fake_app(tmp_path, monkeypatch):
    """A fake release: app_dir() points there; share/ and VERSION under it."""
    share = tmp_path / "share"
    share.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(updates, "app_dir", lambda: tmp_path)
    return tmp_path


# --- download base -----------------------------------------------------------
def test_download_base_from_env(fake_app, monkeypatch):
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", "https://example.com/xyz")
    assert updates.download_base() == "https://example.com/xyz"


def test_download_base_from_file(fake_app, monkeypatch):
    monkeypatch.delenv("FORCE_DOWNLOAD_BASE", raising=False)
    (fake_app / "share" / "download-base").write_text("https://files.example.com/force\n")
    assert updates.download_base() == "https://files.example.com/force"


def test_download_base_missing_or_placeholder(fake_app, monkeypatch):
    monkeypatch.delenv("FORCE_DOWNLOAD_BASE", raising=False)
    assert updates.download_base() is None
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", "https://example.invalid")
    assert updates.download_base() is None
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", "  ")
    assert updates.download_base() is None


# --- force update ------------------------------------------------------------
def test_no_base_exit1(fake_app, monkeypatch, capsys):
    (fake_app / "VERSION").write_text("1.0.0")
    monkeypatch.delenv("FORCE_DOWNLOAD_BASE", raising=False)
    assert updates.update_command() == 1
    err = capsys.readouterr().err
    assert "no download address" in err


def test_dev_build_exit0(fake_app, monkeypatch, capsys, http_server):
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", http_server)   # a base, but no VERSION
    assert updates.update_command() == 0
    assert "development build (deploy.sh): not updated" in capsys.readouterr().out


def test_up_to_date(fake_app, monkeypatch, capsys, http_server):
    (fake_app / "VERSION").write_text("1.0.0")
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", http_server)
    set_body(b'#!/bin/sh\nVERSION="1.0.0"\n')
    assert updates.update_command() == 0
    assert "Force 1.0.0 is up to date" in capsys.readouterr().out


def test_check_newer(fake_app, monkeypatch, capsys, http_server):
    (fake_app / "VERSION").write_text("1.0.0")
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", http_server)
    set_body(b'#!/bin/sh\nVERSION="1.2.3"\n')
    assert updates.update_command(check=True) == 0
    assert capsys.readouterr().out == "force: update available: 1.0.0 -> 1.2.3 (run: force update)\n"


def test_garbage_install_exit1(fake_app, monkeypatch, capsys, http_server):
    (fake_app / "VERSION").write_text("1.0.0")
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", http_server)
    set_body(b'#!/bin/sh\nBOGUS="nope"\n')
    assert updates.update_command() == 1
    assert "no VERSION line" in capsys.readouterr().err


def test_update_runs_sh_with_downloaded_file(fake_app, monkeypatch, capsys, http_server):
    (fake_app / "VERSION").write_text("1.0.0")
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", http_server)
    set_body(b'#!/bin/sh\nVERSION="1.2.3"\n')
    calls = {}

    def fake_run(argv, **kw):
        calls["argv"] = argv
        return SimpleNamespace(returncode=5)

    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    assert updates.update_command() == 5
    out, _ = capsys.readouterr()
    assert "updating 1.0.0 -> 1.2.3 (sessions keep running)" in out
    argv = calls["argv"]
    assert argv[0] == "sh"
    assert argv[2] == "--no-fonts"
    tmp = Path(argv[1])
    assert not tmp.exists(), "the downloaded file is deleted afterwards"


# --- the daemon's background check -------------------------------------------
def test_check_daemon_writes_update_json(fake_app, monkeypatch, tmp_path, http_server):
    state = tmp_path / "state"
    (fake_app / "VERSION").write_text("1.0.0")
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", http_server)
    set_body(b'#!/bin/sh\nVERSION="1.5.0"\n')
    assert updates.check_daemon(state) == "1.5.0"
    data = json.loads((state / "update.json").read_text())
    assert data["latest"] == "1.5.0"
    assert data["checked_ms"] > 0


def test_check_daemon_failure_keeps_old_file(fake_app, monkeypatch, tmp_path, http_server):
    state = tmp_path / "state"
    state.mkdir()
    (state / "update.json").write_text(json.dumps({"latest": "9.9.9", "checked_ms": 1}))
    (fake_app / "VERSION").write_text("1.0.0")
    monkeypatch.setenv("FORCE_DOWNLOAD_BASE", http_server)
    set_body(b'BOGUS')
    assert updates.check_daemon(state) is None
    assert json.loads((state / "update.json").read_text())["latest"] == "9.9.9"
    # no base -> nothing
    monkeypatch.delenv("FORCE_DOWNLOAD_BASE", raising=False)
    assert updates.check_daemon(state) is None


def test_snapshot_update_only_when_newer(fake_app, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (fake_app / "VERSION").write_text("1.0.0")
    (state / "update.json").write_text(json.dumps({"latest": "0.9.0"}))
    assert updates.snapshot_update(state) is None
    (state / "update.json").write_text(json.dumps({"latest": "1.0.0"}))
    assert updates.snapshot_update(state) is None
    (state / "update.json").write_text(json.dumps({"latest": "1.1.0"}))
    assert updates.snapshot_update(state) == "1.1.0"
    (state / "update.json").unlink()
    assert updates.snapshot_update(state) is None
