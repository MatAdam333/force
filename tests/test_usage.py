import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from force.usage import height, markup, normalize, read_usage

UTC = timezone.utc
NOW = 1_000_000.0  # 1970-01-12 13:46:40 UTC
RESET_FUTURE = 2_000_000  # another calendar day in UTC


def usage(**kw):
    """Build a usage dict; absent windows default to None."""
    return {"five_hour": kw.get("five_hour"), "seven_day": kw.get("seven_day")}


# -- normalize -----------------------------------------------------------------------------------
def w(pct, reset):
    return {"used_percentage": pct, "resets_at": reset}


@pytest.mark.parametrize("raw", [None, "str", 5, [], 3.5])
def test_normalize_not_a_dict_is_none(raw):
    assert normalize(raw) is None


def test_normalize_both_missing_is_none():
    assert normalize({}) is None


def test_normalize_one_missing_keeps_the_other():
    assert normalize({"five_hour": w(42, 100)}) == usage(five_hour=w(42.0, 100))
    assert normalize({"seven_day": w(13, 200)}) == usage(seven_day=w(13.0, 200))


def test_normalize_null_window():
    assert normalize({"five_hour": None, "seven_day": w(1, 5)}) == usage(
        seven_day=w(1.0, 5))


def test_normalize_unknown_keys_ignored():
    assert normalize({"five_hour": w(42, 100), "junk": {"x": 1}}) == usage(
        five_hour=w(42.0, 100))


@pytest.mark.parametrize("pct", [True, False])
def test_normalize_bool_percentage_rejected(pct):
    assert normalize({"five_hour": w(pct, 100)}) is None


def test_normalize_bool_resets_at_rejected():
    assert normalize({"five_hour": w(42, True)}) is None


@pytest.mark.parametrize("pct", ["42", None, {}, [], float("nan"), float("inf"),
                                float("-inf")])
def test_normalize_bad_percentage_rejected(pct):
    assert normalize({"five_hour": w(pct, 100)}) is None


@pytest.mark.parametrize("reset", ["100", None, float("nan"), float("inf"),
                                  float("-inf")])
def test_normalize_bad_resets_at_rejected(reset):
    assert normalize({"five_hour": w(42, reset)}) is None


def test_normalize_clamps_percentage():
    assert normalize({"five_hour": w(150, 100)})["five_hour"]["used_percentage"] == 100.0
    assert normalize({"five_hour": w(-5, 100)})["five_hour"]["used_percentage"] == 0.0


def test_normalize_float_resets_at_to_int():
    assert normalize({"five_hour": w(42, 10.7)})["five_hour"]["resets_at"] == 10


def test_normalize_resets_at_zero_invalid():
    assert normalize({"five_hour": w(42, 0)}) is None
    assert normalize({"five_hour": w(42, -3)}) is None


# -- read_usage ---------------------------------------------------------------------------------
def test_read_usage_missing_file(tmp_path):
    assert read_usage(tmp_path / "nope.json") is None


def test_read_usage_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_usage(p) is None


def test_read_usage_good_file(tmp_path):
    p = tmp_path / "good.json"
    p.write_text('{"five_hour": {"used_percentage": 150, "resets_at": 12.0},'
                ' "seven_day": {"used_percentage": 13, "resets_at": 500}}',
                encoding="utf-8")
    assert read_usage(p) == usage(
        five_hour={"used_percentage": 100.0, "resets_at": 12},
        seven_day=w(13.0, 500))


# -- markup ------------------------------------------------------------------------------------
def test_markup_none_is_empty():
    assert markup(None, NOW, 26, UTC) == ""


def test_markup_exact_42_13_at_width_26():
    now = datetime(2025, 4, 18, 12, 0, tzinfo=UTC).timestamp()
    five_r = int(datetime(2025, 4, 19, 18, 0, tzinfo=UTC).timestamp())  # next day
    seven_r = int(datetime(2025, 4, 18, 16, 30, tzinfo=UTC).timestamp())  # same day
    got = markup(
        usage(five_hour=w(42, five_r), seven_day=w(13, seven_r)), now, 26, UTC)
    expected = (
        "[#3a3a3a]──────────────────────────[/]\n"
        "[#808080]session [/][#8a8a8a]█████[/][#3a3a3a]░░░░░░░░[/] [#c6c6c6] 42%[/]\n"
        "[#6c6c6c]        resets Sat 18:00[/]\n"
        "[#808080]week    [/][#8a8a8a]██[/][#3a3a3a]░░░░░░░░░░░[/] [#c6c6c6] 13%[/]\n"
        "[#6c6c6c]        resets 16:30[/]"
    )
    assert got == expected


def _line_a(pct):
    return markup(usage(five_hour=w(pct, RESET_FUTURE)), NOW, 26, UTC).split("\n")[1]


def test_colour_thresholds():
    assert "[#8a8a8a]" in _line_a(69) and "[#c6c6c6] 69%[/]" in _line_a(69)
    assert "[#d7af5f]" in _line_a(70) and "[#d7af5f] 70%[/]" in _line_a(70)
    assert "[#d7af5f]" in _line_a(89) and "[#d7af5f] 89%[/]" in _line_a(89)
    assert "[#e8806f]" in _line_a(90) and "[#e8806f] 90%[/]" in _line_a(90)
    assert "[#e8806f]" in _line_a(100) and "[#e8806f]100%[/]" in _line_a(100)


def test_small_positive_pct_has_at_least_one_cell():
    line = _line_a(0.1)
    filled = line.count("█")
    assert filled == 1


def test_99_6_is_full_bar():
    assert "█" * 13 in _line_a(99.6)


def test_expired_window_shows_dash_and_new_window():
    m = markup(usage(five_hour=w(90, 999_999)), NOW, 26, UTC)
    lines = m.split("\n")
    # percentage "  –" right-justified to 4 -> "   –", bar fully empty
    assert "[#c6c6c6]   –[/]" in lines[1]
    assert lines[1].count("█") == 0
    assert "new window" in lines[2]


def test_reset_text_same_day_vs_other_day():
    now = datetime(2025, 4, 18, 12, 0, tzinfo=UTC).timestamp()
    same = int(datetime(2025, 4, 18, 16, 30, tzinfo=UTC).timestamp())
    other = int(datetime(2025, 4, 19, 18, 0, tzinfo=UTC).timestamp())
    assert "resets 16:30" in markup(usage(five_hour=w(1, same)), now, 26, UTC)
    assert "resets Sat 18:00" in markup(usage(five_hour=w(1, other)), now, 26, UTC)


def test_only_one_window_present():
    m = markup(usage(seven_day=w(13, RESET_FUTURE)), NOW, 26, UTC)
    assert len(m.split("\n")) == 3


def test_height_matches_markup_lines():
    assert height(None) == 0
    for u in (usage(five_hour=w(42, RESET_FUTURE)),
              usage(five_hour=w(42, RESET_FUTURE), seven_day=w(13, RESET_FUTURE))):
        assert height(u) == len(markup(u, NOW, 26, UTC).split("\n"))


def test_lines_never_wider_than_width():
    strip = re.compile(r"\[/?[^\]]*\]")
    for width in (25, 26):
        for pct in (0.1, 1, 42, 69.4, 89.9, 99.6, 100):
            m = markup(usage(five_hour=w(pct, RESET_FUTURE),
                             seven_day=w(pct, RESET_FUTURE)), NOW, width, UTC)
            assert all(len(strip.sub("", line)) <= width for line in m.split("\n"))


def test_normalize_rejects_absurd_reset_time():
    from force import usage
    assert usage.normalize({"five_hour": {"used_percentage": 1, "resets_at": 1e20}}) is None
