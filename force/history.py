"""Build a scrollable history view from a Claude Code transcript or plain shell output.

A share guest (a read-only Unix user watching one agent pane) cannot scroll, because Claude Code draws
on the terminal's alternate screen. The share proxy therefore sends the guest a scrollable history built
from the session's Claude Code transcript (JSONL), or for a plain shell from tmux scrollback. This
module only turns those sources into a list of display entries. It must never raise on bad input.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

Entry = tuple[str, str]   # (kind, text); kind in {"user","agent","tool","result","note","plain"}

_MAX_ENTRY = 20000            # per-entry text cap (chars); a "\n…" line is appended when cut
_MAX_RESULT_LINES = 3         # lines kept from a tool / command result
_TOOL_ARG_LEN = 160           # tool summary argument cap
_TOOL_ARG = ("command", "file_path", "notebook_path", "pattern", "url", "query",
             "description", "prompt", "skill", "path")

# A whole <system-reminder>...</system-reminder> block (greedy-matched text never matters here).
_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_CMD_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_CMD_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_BASH_INPUT = re.compile(r"<bash-input>(.*?)</bash-input>", re.DOTALL)
_RESULT_TAG = re.compile(r"</?(?:local-command-stdout|local-command-stderr|bash-stdout|bash-stderr)>")

# ANSI sequences removed as a whole before control stripping: CSI ESC [ <params> <inter> <final>, and
# OSC ESC ] ... terminated by BEL or ESC \.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# Every remaining C0 control except \n (tab is expanded earlier), DEL, and the C1 range.
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")
_WS = re.compile(r"\s+")


def sanitize(text: str) -> str:
    """Make `text` safe to print to a guest terminal (see module docstring)."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", "    ")
    text = _ANSI.sub("", text)
    return _CTRL.sub("", text)


def _truncate_160(text: str) -> str:
    if len(text) <= _TOOL_ARG_LEN:
        return text
    return text[:_TOOL_ARG_LEN - 1] + "…"


def tool_summary(name: str, inp: object) -> str:
    """One line `Name(arg)` for a tool call (see module docstring)."""
    if isinstance(name, str) and name.startswith("mcp__"):
        parts = name.split("__", 2)
        server = parts[1] if len(parts) >= 2 else ""
        rest = parts[2] if len(parts) >= 3 else ""
        disp = f"{server} · {rest}" if rest else server
    elif isinstance(name, str):
        disp = name
    else:
        disp = "tool"
    if not isinstance(inp, dict):
        return disp
    arg = ""
    for key in _TOOL_ARG:
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            arg = _WS.sub(" ", value).strip()
            break
    if not arg:
        return disp
    arg = _truncate_160(sanitize(arg))
    return f"{disp}({arg})"


def _entry(kind: str, text: str) -> Entry:
    """A (kind, text) pair: sanitize the final text and apply the per-entry length cap."""
    text = sanitize(text)
    if len(text) > _MAX_ENTRY:
        text = text[:_MAX_ENTRY] + "\n…"
    return (kind, text)


def _result_text(content: str, is_error: bool) -> str:
    """The result rule: sanitize, drop blank lines, keep the first 3; then ID-append "+N lines";
    "(no output)" when empty; prefix the first line with "error: " when is_error."""
    s = sanitize(content)
    lines = [ln for ln in s.split("\n") if ln.strip()]
    if not lines:
        return ("error: " if is_error else "") + "(no output)"
    total = len(lines)
    keep = lines[:_MAX_RESULT_LINES]
    if total > _MAX_RESULT_LINES:
        keep.append("… +%d lines" % (total - _MAX_RESULT_LINES))
    if is_error:
        keep[0] = "error: " + keep[0]
    return "\n".join(keep)


def _user_text(out: list, raw: str) -> None:
    """Handle one text payload (a user string content or a {"type":"text"} block)."""
    text = _SYSTEM_REMINDER.sub("", raw)
    text = text.strip()
    if not text:
        return
    if text.startswith("<task-notification>"):
        return
    if _CMD_NAME.search(text):
        x = _CMD_NAME.search(text).group(1)
        am = _CMD_ARGS.search(text)
        if am and am.group(1).strip():
            x += " " + am.group(1).strip()
        out.append(_entry("user", x))
        return
    if _BASH_INPUT.match(text):
        out.append(_entry("user", "! " + _BASH_INPUT.match(text).group(1)))
        return
    if text.startswith("<local-command-stdout>") or text.startswith("<bash-stdout>"):
        stripped = _RESULT_TAG.sub("", text).strip()
        if stripped:
            out.append(_entry("result", _result_text(stripped, False)))
        return
    out.append(_entry("user", text))


def _user_result(out: list, block: dict) -> None:
    is_error = bool(block.get("is_error"))
    content = block.get("content")
    if isinstance(content, str):
        joined = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, dict) and item.get("type") == "image":
                parts.append("[image]")
        joined = "\n".join(parts)
    else:
        joined = ""
    out.append(_entry("result", _result_text(joined, is_error)))


def _user_record(record: dict, out: list) -> None:
    if record.get("isCompactSummary"):
        out.append(_entry("note", "conversation compacted"))
        return
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        _user_text(out, content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                _user_text(out, str(block.get("text", "")))
            elif btype == "image":
                out.append(_entry("user", "[image]"))
            elif btype == "tool_result":
                _user_result(out, block)


def _assistant_record(record: dict, out: list) -> None:
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        if content.strip():
            out.append(_entry("agent", content.strip()))
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = block.get("text", "")
            if isinstance(t, str) and t.strip():
                out.append(_entry("agent", t.strip()))
        elif btype == "tool_use":
            out.append(_entry("tool", tool_summary(block.get("name"), block.get("input"))))


def render_transcript(path: Path, max_bytes: int = 4 * 1024 * 1024, max_entries: int = 2000) -> list[Entry]:
    """Display entries for the last ≤`max_bytes` of the session's Claude Code transcript."""
    try:
        st = path.stat()
    except OSError:
        return []
    size = st.st_size
    truncated = size > max_bytes
    try:
        with open(path, "rb") as f:
            if truncated:
                f.seek(size - max_bytes)
            buf = f.read()
    except OSError:
        return []
    text = buf.decode("utf-8", errors="replace")
    lines = text.split("\n")
    entries: list[Entry] = []
    if truncated:
        if lines:
            lines = lines[1:]        # drop the partial line cut at the seek boundary
        entries.append(_entry("note", "earlier history not shown"))
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue                # malformed line: ignore
        if not isinstance(record, dict):
            continue
        if record.get("isSidechain") or record.get("isMeta"):
            continue
        rtype = record.get("type")
        if rtype == "user":
            _user_record(record, entries)
        elif rtype == "assistant":
            _assistant_record(record, entries)
    if len(entries) > max_entries:
        entries = entries[-max_entries:]
        entries[0] = _entry("note", "earlier history not shown")
    return entries


def render_plain(text: str, max_entries: int = 5000) -> list[Entry]:
    """Display entries for a plain shell pane's tmux scrollback (one "plain" entry per line)."""
    if not isinstance(text, str):
        return []
    lines = [ln.rstrip() for ln in sanitize(text).split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    entries = [_entry("plain", ln) for ln in lines]
    if len(entries) > max_entries:
        entries = entries[-max_entries:]
        entries[0] = _entry("note", "earlier history not shown")
    return entries
