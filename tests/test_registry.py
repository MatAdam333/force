import json
import multiprocessing
import uuid

import pytest

from force.registry import (Entry, Registry, RegistryError, is_shell, load, locked,
                          save, slugify, valid_name)


def make_entry(name="portal", order=1, **kw):
    d = {"name": name, "session_id": str(uuid.uuid4()), "cwd": "/home/alice",
         "created_at": "2026-10-07T10:00:00+00:00", "order": order,
         "archived": False, "archived_at": None, "pending_name": None}
    d.update(kw)
    keys = ("name", "session_id", "cwd", "created_at", "order",
            "archived", "archived_at", "pending_name")
    return Entry(**{k: v for k, v in d.items() if k in keys})


# --- valid_name / slugify ---------------------------------------------------
def test_is_shell():
    assert not is_shell(make_entry())
    claude = make_entry()
    claude.extra["kind"] = "claude"
    assert not is_shell(claude)
    shell = make_entry()
    shell.extra["kind"] = "shell"
    assert is_shell(shell)


def test_valid_name():
    assert valid_name("portal")
    assert valid_name("a")
    assert valid_name("ab-cd0")
    assert not valid_name("Portal")
    assert not valid_name("portal!")
    assert not valid_name("-portal")
    assert not valid_name("")
    assert not valid_name("x" * 32)
    assert valid_name("x" * 31)


@pytest.mark.parametrize("raw,expected", [
    ("Portal Generation!!", "portal-generation"),
    ("---", None),
    ("ünïcode", "n-code"),
    ("HELLO WORLD", "hello-world"),
    ("", None),
    ("  spaced  out  ", "spaced-out"),
    ("x" * 40, ("x" * 31)),
    ("hello-", "hello"),
])
def test_slugify(raw, expected):
    assert slugify(raw) == expected


def test_slugify_40char_with_dashes():
    # 40 chars that truncate to 31 then strip trailing dash
    s = "a" * 31 + "-" + "b" * 8
    assert slugify(s) == "a" * 31


# --- load -------------------------------------------------------------------
def test_load_missing_empty(tmp_path):
    reg = load(tmp_path / "registry.json")
    assert reg.entries == []
    assert reg.live() == []


def test_load_bad_json_raises(tmp_path):
    f = tmp_path / "registry.json"
    f.write_text("{not json")
    with pytest.raises(RegistryError):
        load(f)


def test_load_top_level_not_dict(tmp_path):
    f = tmp_path / "registry.json"
    f.write_text("[1,2,3]")
    with pytest.raises(RegistryError):
        load(f)


def test_load_sessions_not_list(tmp_path):
    f = tmp_path / "registry.json"
    f.write_text('{"version":1,"sessions":{"x":1}}')
    with pytest.raises(RegistryError):
        load(f)


def test_load_missing_required_key(tmp_path):
    f = tmp_path / "registry.json"
    f.write_text(json.dumps({"version": 1, "sessions": [{"name": "portal"}]}))
    with pytest.raises(RegistryError):
        load(f)


def test_load_unknown_keys_to_extra(tmp_path):
    f = tmp_path / "registry.json"
    data = {"version": 1, "sessions": [{
        "name": "portal", "session_id": str(uuid.uuid4()), "cwd": "/home",
        "created_at": "now", "order": 1, "fancy": "keep", "other": 3,
    }]}
    f.write_text(json.dumps(data))
    reg = load(f)
    assert reg.entries[0].extra == {"fancy": "keep", "other": 3}
    save(f, reg)
    saved = json.loads(f.read_text())
    assert saved["sessions"][0]["fancy"] == "keep"
    assert saved["sessions"][0]["other"] == 3


# --- save -------------------------------------------------------------------
def test_save_atomic_and_shape(tmp_path):
    reg = Registry([make_entry()])
    p = tmp_path / "registry.json"
    save(p, reg)
    data = json.loads(p.read_text())
    assert data["version"] == 1
    assert len(data["sessions"]) == 1
    assert list(data["sessions"][0]) == [
        "name", "session_id", "cwd", "created_at", "order",
        "archived", "archived_at", "pending_name",
    ]
    assert not (tmp_path / "registry.json.tmp").exists()


# --- Registry methods -------------------------------------------------------
def test_get_by_session_id():
    sid = str(uuid.uuid4())
    reg = Registry([make_entry(name="a", order=1, session_id=sid),
                    make_entry(name="b", order=2)])
    assert reg.get("a").name == "a"
    assert reg.get("nope") is None
    assert reg.by_session_id(sid).name == "a"
    assert reg.by_session_id("nope") is None


def test_live_and_archived_sorting():
    reg = Registry([
        make_entry(name="c", order=3, archived=True, archived_at="2026-01-02"),
        make_entry(name="a", order=1),
        make_entry(name="b", order=2, archived=True, archived_at="2026-01-03"),
    ])
    assert [e.name for e in reg.live()] == ["a"]
    assert [e.name for e in reg.archived()] == ["b", "c"]


def test_name_taken():
    reg = Registry([make_entry(name="a", pending_name="z")])
    assert reg.name_taken("a")
    assert reg.name_taken("z")
    assert not reg.name_taken("b")


def test_add_valid():
    reg = Registry([make_entry(order=5)])
    e = reg.add("new", str(uuid.uuid4()), "/tmp")
    assert e.order == 6
    assert reg.get("new") is e


def test_add_first_order_zero():
    reg = Registry([])
    e = reg.add("first", str(uuid.uuid4()), "/tmp")
    assert e.order == 0


def test_add_invalid_name(tmp_path):
    reg = Registry([])
    with pytest.raises(RegistryError):
        reg.add("Bad Name", str(uuid.uuid4()), "/tmp")


def test_add_taken_name():
    reg = Registry([make_entry(name="a")])
    with pytest.raises(RegistryError):
        reg.add("a", str(uuid.uuid4()), "/tmp")


def test_add_taken_pending_name():
    reg = Registry([make_entry(name="a", pending_name="b")])
    with pytest.raises(RegistryError):
        reg.add("b", str(uuid.uuid4()), "/tmp")


def test_add_invalid_uuid():
    reg = Registry([])
    with pytest.raises(RegistryError):
        reg.add("new", "not-a-uuid", "/tmp")


def test_add_cwd_not_absolute():
    reg = Registry([])
    with pytest.raises(RegistryError):
        reg.add("new", str(uuid.uuid4()), "relative/path")


def test_rename():
    reg = Registry([make_entry(name="old", pending_name="zzz")])
    reg.rename("old", "new")
    assert reg.get("new") is not None
    assert reg.get("new").pending_name is None
    assert reg.get("old") is None


def test_rename_unknown():
    reg = Registry([])
    with pytest.raises(RegistryError):
        reg.rename("old", "new")


def test_rename_invalid_new():
    reg = Registry([make_entry(name="old")])
    with pytest.raises(RegistryError):
        reg.rename("old", "Bad")


def test_rename_taken():
    reg = Registry([make_entry(name="a"), make_entry(name="b")])
    with pytest.raises(RegistryError):
        reg.rename("a", "b")


def test_remove():
    reg = Registry([make_entry(name="a")])
    reg.remove("a")
    assert reg.entries == []
    with pytest.raises(RegistryError):
        reg.remove("a")


# --- locked ----------------------------------------------------------------
def test_locked_creates_home_and_saves_when_modified(tmp_path):
    home = tmp_path / "home"
    with locked(home) as reg:
        reg.add("portal", str(uuid.uuid4()), "/tmp")
    assert (home / "registry.lock").exists()
    data = json.loads((home / "registry.json").read_text())
    assert data["sessions"][0]["name"] == "portal"


def test_locked_no_save_when_unchanged(tmp_path):
    home = tmp_path / "home"
    reg_outer = Registry([make_entry()])
    save(home / "registry.json", reg_outer)
    before = (home / "registry.json").read_text()
    with locked(home) as reg:
        pass  # no modification
    assert (home / "registry.json").read_text() == before


def test_locked_no_save_on_exception(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(RuntimeError):
        with locked(home) as reg:
            reg.add("portal", str(uuid.uuid4()), "/tmp")
            raise RuntimeError("boom")
    assert not (home / "registry.json").exists()


def _worker(home, n):
    for i in range(n):
        with locked(home) as reg:
            reg.add(f"w{multiprocessing.current_process().name}{i}".lower()[:31].replace("-", "0"),
                    str(uuid.uuid4()), "/tmp")


def test_locked_concurrency(tmp_path):
    home = tmp_path / "home"
    p1 = multiprocessing.Process(target=_worker, args=(home, 50), name="a")
    p2 = multiprocessing.Process(target=_worker, args=(home, 50), name="b")
    p1.start(); p2.start()
    p1.join(60); p2.join(60)
    assert p1.exitcode == 0
    assert p2.exitcode == 0
    data = json.loads((home / "registry.json").read_text())
    assert len(data["sessions"]) == 100
    names = {e["name"] for e in data["sessions"]}
    assert len(names) == 100  # all unique


# --- history (SPEC §43.5) ---------------------------------------------------
import os as _os  # noqa: E402
import time as _time  # noqa: E402


def _reg_with(n):
    return Registry([make_entry(name=f"s{i}", order=i) for i in range(n)])


def test_save_snapshots_throttled(tmp_path):
    f = tmp_path / "registry.json"
    snaps = tmp_path / "registry-snapshots"
    save(f, _reg_with(1))
    assert len(list(snaps.iterdir())) == 1
    save(f, _reg_with(2))                      # within 15 min
    assert len(list(snaps.iterdir())) == 1
    old = _time.time() - 20 * 60
    only = next(snaps.iterdir())
    _os.utime(only, (old, old))
    only.rename(snaps / "registry-20000101-000000.json")
    _os.utime(snaps / "registry-20000101-000000.json", (old, old))
    save(f, _reg_with(2))
    assert len(list(snaps.iterdir())) == 2
    newest = sorted(snaps.iterdir())[-1]
    assert json.loads(newest.read_text())["sessions"][1]["name"] == "s1"


def test_save_snapshot_skips_equal_content_and_caps(tmp_path):
    f = tmp_path / "registry.json"
    snaps = tmp_path / "registry-snapshots"
    reg = _reg_with(1)                         # one object: _reg_with makes new session ids each call
    save(f, reg)
    first = next(snaps.iterdir())
    old = _time.time() - 3600
    _os.utime(first, (old, old))
    save(f, reg)                               # aged, but equal content
    assert len(list(snaps.iterdir())) == 1
    first.rename(snaps / "registry-20000101-000000.json")
    snaps.mkdir(exist_ok=True)
    for i in range(60):
        p = snaps / f"registry-2001{i // 28 + 1:02d}{i % 28 + 1:02d}-000000.json"
        p.write_text("{}")
        _os.utime(p, (old, old))
    save(f, _reg_with(3))
    names = sorted(p.name for p in snaps.iterdir())
    assert len(names) == 48
    assert names[-1] > "registry-2002"           # the new copy is kept, the oldest are gone


def test_corrupt_load_backs_up_once(tmp_path):
    f = tmp_path / "registry.json"
    f.write_text("{not json")
    for _ in range(2):
        with pytest.raises(RegistryError):
            load(f)
    backups = list((tmp_path / "registry-backups").iterdir())
    assert len(backups) == 1 and backups[0].read_text() == "{not json"
    f.write_text("[1]")                        # bad shape, other content
    with pytest.raises(RegistryError):
        load(f)
    assert len(list((tmp_path / "registry-backups").iterdir())) <= 3
