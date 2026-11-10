import json

from force import history


def write(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def user(content, **kw):
    r = {"type": "user", "message": {"content": content}}
    r.update(kw)
    return r


def assistant(blocks):
    return {"type": "assistant", "message": {"content": blocks}}


# --- sanitize ---------------------------------------------------------------

def test_sanitize_ansi_csi_osc_esc():
    assert history.sanitize("\x1b[31mred\x1b[0m") == "red"
    assert history.sanitize("\x1b[32mgreen\x1b[0m") == "green"
    assert history.sanitize("a\x1b]0;title\x07b") == "ab"
    assert history.sanitize("a\x1b]8;;url\x1b\\b") == "ab"
    assert history.sanitize("left\x1bright") == "leftright"
    assert "\x1b" not in history.sanitize("\x1b\x1b\x1b")


def test_sanitize_controls_and_del():
    for bad in "\x00\x01\x08\x0b\x0c\x0e\x15\x1f\x7f\x80\x9f":
        assert bad not in history.sanitize("a" + bad + "b")


def test_sanitize_keeps_newlines_tabs_unicode():
    assert history.sanitize("a\tb") == "a    b"
    assert history.sanitize("a\r\nb\rc") == "a\nb\nc"
    assert history.sanitize("é漢😀\nline") == "é漢😀\nline"


def test_sanitize_non_str():
    assert history.sanitize(None) == ""
    assert history.sanitize(123) == ""
    assert history.sanitize(["x"]) == ""


# --- tool_summary -----------------------------------------------------------

def test_tool_bash_collapses_whitespace():
    assert history.tool_summary("Bash", {"command": "ls   -la  /tmp"}) == "Bash(ls -la /tmp)"


def test_tool_read_file_path():
    assert history.tool_summary("Read", {"file_path": "/home/a/b.py"}) == "Read(/home/a/b.py)"
    assert history.tool_summary("Read", {"query": "x", "file_path": "/f"}) == "Read(/f)"


def test_tool_mcp_name():
    assert history.tool_summary("mcp__server__my_tool", {"query": "x"}) == "server · my_tool(x)"
    assert history.tool_summary("mcp__server__a__b", {}) == "server · a__b"


def test_tool_long_arg_truncated():
    assert history.tool_summary("Bash", {"command": "x" * 200}) == "Bash(" + "x" * 159 + "…)"
    assert len(history.tool_summary("Bash", {"command": "x" * 200})) == 166


def test_tool_missing_input():
    assert history.tool_summary("Bash", {}) == "Bash"
    assert history.tool_summary("Bash", None) == "Bash"
    assert history.tool_summary("Bash", "not a dict") == "Bash"
    assert history.tool_summary("Bash", []) == "Bash"


def test_tool_non_str_name():
    assert history.tool_summary(None, {}) == "tool"
    assert history.tool_summary(42, {"command": "ls"}) == "tool(ls)"


# --- render_transcript ------------------------------------------------------

def test_transcript_missing_file(tmp_path):
    assert history.render_transcript(tmp_path / "nope.jsonl") == []


def test_transcript_junk_lines(tmp_path):
    p = write(tmp_path / "junk.jsonl", [
        {"type": "user", "message": {"content": "hi"}},
        "notjson",
        42,
        {},
        [],
    ])
    assert history.render_transcript(p) == [("user", "hi")]


def test_transcript_sidechain_and_meta_skipped(tmp_path):
    p = write(tmp_path / "sm.jsonl", [
        user("a", isSidechain=True),
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "b"}]}},
        user("c", isMeta=True),
        user("shown"),
    ])
    assert history.render_transcript(p) == [("user", "shown")]


def test_transcript_compact_summary_note(tmp_path):
    p = write(tmp_path / "compact.jsonl", [
        user("ignored", isCompactSummary=True),
        assistant([{"type": "text", "text": "after"}]),
    ])
    assert history.render_transcript(p) == [("note", "conversation compacted"), ("agent", "after")]


def test_transcript_system_reminder_stripped(tmp_path):
    p = write(tmp_path / "rem.jsonl", [
        user("<system-reminder>REM</system-reminder>hello <system-reminder><b>x</b></system-reminder>world"),
        user("<system-reminder>only a reminder</system-reminder>"),
    ])
    assert history.render_transcript(p) == [("user", "hello world")]


def test_transcript_task_notification_skipped(tmp_path):
    p = write(tmp_path / "task.jsonl", [
        user("<task-notification>new task</task-notification>"),
        user("<task-notification>t</task-notification>extra"),
        user("shown"),
    ])
    assert history.render_transcript(p) == [("user", "shown")]


def test_transcript_command_name_with_and_without_args(tmp_path):
    p = write(tmp_path / "cmd.jsonl", [
        user("<command-name>/clear</command-name>"),
        user("<command-name>/model</command-name><command-args>  opus   </command-args>"),
    ])
    assert history.render_transcript(p) == [
        ("user", "/clear"),
        ("user", "/model opus"),
    ]


def test_transcript_bash_input(tmp_path):
    p = write(tmp_path / "bi.jsonl", [
        user("<bash-input>ls -la</bash-input>"),
    ])
    assert history.render_transcript(p) == [("user", "! ls -la")]


def test_transcript_bash_stdout_result(tmp_path):
    p = write(tmp_path / "bo.jsonl", [
        user("<bash-stdout>a\nb\nc\nd\ne\n</bash-stdout>"),
    ])
    assert history.render_transcript(p) == [("result", "a\nb\nc\n… +2 lines")]


def test_transcript_stdout_empty_skipped(tmp_path):
    p = write(tmp_path / "eo.jsonl", [
        user("<local-command-stdout>\n</local-command-stdout>"),
    ])
    assert history.render_transcript(p) == []


def test_transcript_tool_result_variants(tmp_path):
    p = write(tmp_path / "tr.jsonl", [
        user([{"type": "tool_result", "content": "out1\nout2"}]),
        user([{"type": "tool_result", "content": [{"type": "image", "source": {}}]}]),
        user([{"type": "tool_result", "content": "boom", "is_error": True}]),
        user([{"type": "tool_result", "content": "1\n2\n3\n4\n5"}]),
        user([{"type": "tool_result", "content": ""}]),
    ])
    assert history.render_transcript(p) == [
        ("result", "out1\nout2"),
        ("result", "[image]"),
        ("result", "error: boom"),
        ("result", "1\n2\n3\n… +2 lines"),
        ("result", "(no output)"),
    ]


def test_transcript_thinking_skipped(tmp_path):
    p = write(tmp_path / "th.jsonl", [
        assistant([
            {"type": "thinking", "thinking": "secret"},
            {"type": "redacted_thinking", "data": "x"},
            {"type": "text", "text": "visible"},
        ]),
    ])
    assert history.render_transcript(p) == [("agent", "visible")]


def test_transcript_assistant_order_and_esc_removed(tmp_path):
    p = write(tmp_path / "ao.jsonl", [
        assistant([
            {"type": "text", "text": "\x1b[32mstart\x1b[0m"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "text", "text": "done"},
        ]),
    ])
    assert history.render_transcript(p) == [
        ("agent", "start"),
        ("tool", "Bash(ls)"),
        ("agent", "done"),
    ]


def test_transcript_max_bytes_adds_note_and_drops_partial(tmp_path):
    p = write(tmp_path / "mb.jsonl", [
        user("first"),
        user("second"),
        user("third"),
    ])
    entries = history.render_transcript(p, max_bytes=10)
    assert entries[0] == ("note", "earlier history not shown")
    assert ("user", "first") not in entries


def test_transcript_max_bytes_parses_lines_after_partial(tmp_path):
    p = write(tmp_path / "mb2.jsonl", [
        user("x" * 1000),
        user("aftercut"),
    ])
    entries = history.render_transcript(p, max_bytes=200)
    assert entries[0] == ("note", "earlier history not shown")
    assert ("user", "aftercut") in entries


def test_transcript_max_entries_cap(tmp_path):
    records = [assistant([{"type": "text", "text": "m%d" % i}]) for i in range(10)]
    p = write(tmp_path / "me.jsonl", records)
    entries = history.render_transcript(p, max_entries=4)
    assert len(entries) == 4
    assert entries[0] == ("note", "earlier history not shown")
    assert entries[-1] == ("agent", "m9")


def test_transcript_cap_no_double_note_when_truncated(tmp_path):
    p = write(tmp_path / "nd.jsonl", [
        user("x" * 500),
        assistant([{"type": "text", "text": "a"}]),
        assistant([{"type": "text", "text": "b"}]),
        assistant([{"type": "text", "text": "c"}]),
    ])
    entries = history.render_transcript(p, max_bytes=100, max_entries=2)
    assert len(entries) == 2
    assert entries[0] == ("note", "earlier history not shown")
    assert sum(1 for k, _ in entries if k == "note") == 1


# --- render_plain -----------------------------------------------------------

def test_plain_basics():
    assert history.render_plain("  a line  \n\nb\n\n\n") == [
        ("plain", "  a line"),
        ("plain", ""),
        ("plain", "b"),
    ]


def test_plain_sanitizes():
    assert history.render_plain("a\x1b[31mb\x1b[0mc") == [("plain", "abc")]


def test_plain_cap():
    text = "\n".join("line%d" % i for i in range(10))
    entries = history.render_plain(text, max_entries=4)
    assert len(entries) == 4
    assert entries[0] == ("note", "earlier history not shown")
    assert entries[-1] == ("plain", "line9")


def test_plain_empty():
    assert history.render_plain("") == []
    assert history.render_plain("   \n\n  ") == []
    assert history.render_plain(None) == []
