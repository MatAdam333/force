"""Tests for share/force-join: the guest client's local history pager (pure functions only, no tty)."""
import importlib.machinery
import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JOIN = REPO / "share" / "force-join"


@pytest.fixture(scope="module")
def mod():
    loader = importlib.machinery.SourceFileLoader("cockpit_join", str(JOIN))
    spec = importlib.util.spec_from_loader("cockpit_join", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def strip_sgr(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# ------------------------------------------------------------------- parse_keys
def test_parse_keys_every_key(mod):
    assert mod.parse_keys(b"\x1b[A") == ["up"]
    assert mod.parse_keys(b"\x1bOA") == ["up"]
    assert mod.parse_keys(b"\x1b[B") == ["down"]
    assert mod.parse_keys(b"\x1bOB") == ["down"]
    assert mod.parse_keys(b"\x1b[5~") == ["pgup"]
    assert mod.parse_keys(b"\x1b[6~") == ["pgdn"]
    assert mod.parse_keys(b"\x1b[H") == ["home"]
    assert mod.parse_keys(b"\x1b[1~") == ["home"]
    assert mod.parse_keys(b"\x1bOH") == ["home"]
    assert mod.parse_keys(b"\x1b[F") == ["end"]
    assert mod.parse_keys(b"\x1b[4~") == ["end"]
    assert mod.parse_keys(b"\x1bOF") == ["end"]
    assert mod.parse_keys(b"\x1b") == ["esc"]
    assert mod.parse_keys(b"\x03") == ["ctrl-c"]
    assert mod.parse_keys(b"\x11") == ["ctrl-q"]
    assert mod.parse_keys(b"\x1d") == ["ctrl-]"]
    assert mod.parse_keys(b"q") == ["q"]
    assert mod.parse_keys(b"k") == ["k"]
    assert mod.parse_keys(b" ") == [" "]


def test_parse_keys_combined_and_split(mod):
    assert mod.parse_keys(b"\x1b[A\x1b[A") == ["up", "up"]
    assert mod.parse_keys(b"kq") == ["k", "q"]
    assert mod.parse_keys(b"\x1b\x1b") == ["esc", "esc"]


def test_parse_keys_unknown_csi(mod):
    assert mod.parse_keys(b"\x1b[10;5H") == ["unknown"]
    assert mod.parse_keys(b"\x1b[3~") == ["unknown"]


# ---------------------------------------------------------------- display_width
def test_display_width(mod):
    assert mod.display_width("abc") == 3
    assert mod.display_width("漢") == 2
    assert mod.display_width("á") == 1          # combining = 0
    assert mod.display_width("́") == 0
    assert mod.display_width("ab漢") == 4


# ---------------------------------------------------------------------- layout
def test_layout_no_row_wider_than_cols(mod):
    rows = mod.layout([["plain", "hello world this is a long sentence that wraps"]], 10)
    assert rows
    for row in rows:
        assert mod.display_width(strip_sgr(row)) <= 10


def test_layout_wide_chars_never_split(mod):
    rows = mod.layout([["plain", "漢漢漢漢漢"]], 5)   # each 漢 = 2 cols
    # no row is wider than cols, so a 2-col wide char never straddles the edge
    for row in rows:
        assert mod.display_width(strip_sgr(row)) <= 5
    assert "".join(strip_sgr(r) for r in rows) == "漢漢漢漢漢"


def test_layout_prefix_and_continuation_indent(mod):
    rows = mod.layout([["user", "some text that should wrap across lines"]], 10)
    stripped = [strip_sgr(r) for r in rows]
    assert any(r.startswith("> ") for r in stripped)          # user prefix
    assert any(r.startswith("  ") and not r.startswith("> ") for r in stripped)  # continuation


def test_layout_blank_line_between_entries(mod):
    rows = mod.layout([["user", "a"], ["plain", "b"]], 20)
    assert "" in [strip_sgr(r) for r in rows]


def test_layout_no_blank_tool_to_result(mod):
    rows = mod.layout([["tool", "run"], ["result", "ok"]], 20)
    stripped = [strip_sgr(r) for r in rows]
    assert "" not in stripped


def test_layout_no_blank_between_plain(mod):
    rows = mod.layout([["plain", "a"], ["plain", "b"]], 20)
    stripped = [strip_sgr(r) for r in rows]
    assert "" not in stripped


def test_layout_newline_handling(mod):
    rows = mod.layout([["plain", "line1\nline2\nline3"]], 20)
    stripped = [strip_sgr(r) for r in rows]
    assert stripped == ["line1", "line2", "line3"]


def test_layout_control_chars_stripped(mod):
    rows = mod.layout([["plain", "ab\x07cd\x7f ef\x1b[x"]], 20)
    stripped = [strip_sgr(r) for r in rows]
    assert stripped == ["abcd ef[x"]


def test_layout_unknown_kind_renders_like_plain(mod):
    rows = mod.layout([["bogus", "hello"]], 20)
    assert strip_sgr(rows[0]) == "hello"


# ------------------------------------------------------------------------ Pager
def _pager(mod, n=30, rows=10, cols=30):
    entries = [["plain", f"row {i:02d}"] for i in range(n)]   # plain -> no blank lines
    p = mod.Pager(rows, cols)
    p.set_entries(entries)
    return p


def test_pager_starts_at_bottom(mod):
    p = _pager(mod)
    assert p.content_rows == 30
    assert p.top == 30 - (p.rows - 1)          # scrolled to the bottom


def test_pager_scroll_and_clamp(mod):
    p = _pager(mod)
    max_top = p.top
    p.key("up")
    assert p.top == max_top - 1
    p.key("down")
    assert p.top == max_top
    p.key("down")                                # down at bottom does not leave live
    assert p.top == max_top
    p.key("home")
    assert p.top == 0
    p.key("up")                                 # clamp at top
    assert p.top == 0
    p.key("end")
    assert p.top == max_top


def test_pager_page_jumps(mod):
    p = _pager(mod)
    max_top = p.top
    p.key("home")
    p.key("pgdn")
    assert p.top == p.rows - 2
    p.key("home")
    p.key("pgup")
    assert p.top == 0


def test_pager_key_modes(mod):
    p = _pager(mod)
    assert p.key("q") == "live"
    assert p.key("esc") == "live"
    assert p.key("ctrl-c") == "live"
    assert p.key("ctrl-q") == "leave"
    assert p.key("ctrl-]") == "leave"
    assert p.key("up") is None
    assert p.key("x") is None                    # unknown keys ignored


def test_pager_resize_keeps_distance_from_bottom(mod):
    p = _pager(mod)
    p.key("up")                                  # one line above bottom
    dist = p.content_rows - (p.top + p.rows - 1)
    p.resize(20, 30)
    new_dist = p.content_rows - (p.top + p.rows - 1)
    assert new_dist == dist


def test_pager_empty_entries(mod):
    p = mod.Pager(10, 30)
    p.set_entries([])
    assert "no history yet" in p.screen({}).decode()


def test_pager_screen_contains_status(mod):
    p = mod.Pager(10, 200)
    p.set_entries([["user", "hi"]])
    out = p.screen({"owner": "alice", "session": "sess", "mode": "read-only"}).decode()
    assert "history · alice/sess" in out
    assert "q back to live" in out
    assert "Ctrl+Q leaves" in out


# ---------------------------------------------- end-to-end (pure, no fake tty)
def test_main_loop_helpers_are_pure(mod):
    # The pager + layout + parse_keys drive the whole history UI without a terminal.
    entries = [["user", "check"], ["tool", "run"], ["result", "ok"]]
    p = mod.Pager(10, 40)
    p.set_entries(entries)
    assert p.top >= 0
    info = {"owner": "alice", "session": "s", "mode": "read-write"}
    out = p.screen(info)
    assert out.startswith(b"\x1b[1;1H")
    assert mod.parse_keys(b"\x1b[5~") == ["pgup"]
    assert mod.layout([["bogus", "x"]], 20)   # unknown kinds don't crash


def test_layout_keeps_blank_lines_inside_a_message(mod):
    rows = [strip_sgr(r).rstrip() for r in mod.layout([("agent", "para one\n\npara two")], 40)]
    assert rows == ["● para one", "", "  para two"]
