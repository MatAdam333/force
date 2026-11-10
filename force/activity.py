"""One-line "what is this session doing" text derived from a Claude Code transcript.

A Claude Code transcript (SPEC §4) is a JSONL file (one JSON object per line). `describe` reads the
tail of the file and walks records backwards to find the newest assistant turn that carries a concrete action
(a tool_use or text block), and renders it as a single short line for the sidebar (SPEC §15/§16).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_TAIL = 256 * 1024           # read at most this many bytes from the end of the transcript

# Leading markdown markers to strip from a text block's first line: # heading, * bullet, - bullet,
# > quote, ` code-span, and `1.` numbered-list items.
_MD_LEAD = re.compile(r"^(?:#{1,6}\s*|[*>\-\s]+|\d+\.\s*|`+)")
_WS = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# A trailing publish date (-YYYYMMDD) on a model id, and the family+version pattern it exposes.
_MODEL_DATE = re.compile(r"-\d{8}$")
_MODEL = re.compile(r"^([A-Za-z]+)((?:-\d+)+)$")


def _single(text: str) -> str:
    """Whitespace-collapse and strip control characters from `text`."""
    text = _CONTROL.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _first_line(text: str) -> str | None:
    """First non-empty line of `text`, with leading markdown markers stripped and whitespace collapsed."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = _MD_LEAD.sub("", line)
        line = line.replace("**", "")          # remove bold ** pairs
        line = _single(line)
        if not line:
            continue
        return line
    return None


def _basename(path: str) -> str:
    if not path:
        return ""
    return os.path.basename(str(path))


def _file(inp: dict) -> str:
    return _basename(str(inp.get("file_path") or inp.get("notebook_path") or ""))


def _tool_desc(block: dict) -> str | None:
    """A short, complete phrase from a fixed vocabulary - never a cut-off sentence (the sidebar shows
    about 20 characters). Only file names carry detail."""
    name = str(block.get("name") or "")
    inp = block.get("input")
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        return "running a command"
    if name in ("Read", "NotebookRead"):
        return ("reading " + _file(inp)) if _file(inp) else "reading files"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return ("editing " + _file(inp)) if _file(inp) else "editing files"
    if name == "Write":
        return ("writing " + _file(inp)) if _file(inp) else "writing files"
    if name in ("Grep", "Glob"):
        return "searching files"
    if name in ("Agent", "Task"):
        return "delegating"
    if name == "AskUserQuestion":
        return "asks you a question"
    if name == "ExitPlanMode":
        return "plan to review"
    if name in ("WebFetch", "WebSearch"):
        return "browsing the web"
    if name == "TodoWrite":
        return "planning"
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        return "using " + (parts[1] if len(parts) >= 2 and parts[1] else "a tool")
    return "using " + name.lower() if name else None


def _truncate(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len == 1:
        return "…"
    return text[: max_len - 1] + "…"


def _describe_blocks(blocks: list) -> str | None:
    """Phrase for one assistant record: its last tool_use block if any, else "writing a reply" for a
    text block, else "thinking" for a thinking-only record."""
    tool = None
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool = block
    if tool is not None:
        return _tool_desc(tool)
    types = {b.get("type") for b in blocks if isinstance(b, dict)}
    if "text" in types:
        return "writing a reply"
    if "thinking" in types or "redacted_thinking" in types:
        return "thinking"
    return None


def _model_short(model: object) -> str | None:
    """Short label for a model id: strip a leading `claude-`, drop a trailing `-YYYYMMDD` date, then join
    the family and dotted version parts ("claude-opus-5-5" -> "opus 5.5"). Anything that no longer
    matches `<family>-<digits>(-<digits>)*` is returned as the stripped id unchanged. None for a missing or
    non-string value."""
    if not isinstance(model, str) or not model:
        return None
    stripped = model[len("claude-"):] if model.startswith("claude-") else model
    base = _MODEL_DATE.sub("", stripped)
    m = _MODEL.match(base)
    if m:
        family = m.group(1)
        version = ".".join(m.group(2).split("-")[1:])
        return f"{family} {version}"
    return stripped


def _scan_tail(transcript: Path) -> list | None:
    """The record lines of the last ≤256 KiB of `transcript` (newest last), or None if unreadable."""
    try:
        st = transcript.stat()
        size = st.st_size
        with open(transcript, "rb") as f:
            if size > _TAIL:
                f.seek(size - _TAIL)
            buf = f.read()
    except OSError:
        return None
    text = buf.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > _TAIL and lines:
        lines = lines[1:]        # drop the partial first line cut at the 256 KiB seek boundary
    return lines


def scan(transcript: Path, max_len: int = 60) -> tuple[str | None, str | None, str | None]:
    """(activity, model_short, effort) from the tail of `transcript`: `activity` from the newest concrete
    assistant turn, and `model_short`/`effort` from the newest non-sidechain assistant record. All three are
    read from the file in a single pass. model/effort are None when the file is missing/unreadable or no
    suitable assistant record is found."""
    lines = _scan_tail(transcript)
    if lines is None:
        return None, None, None
    model = effort = None
    newest = False
    desc = None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue            # malformed line: ignore
        if not isinstance(record, dict):
            continue
        if record.get("type") != "assistant":
            continue
        if record.get("isSidechain"):
            continue
        message = record.get("message")
        # Claude Code records API errors (e.g. no network right after a reboot) as assistant messages
        # with model "<synthetic>": not a real turn, so they never set the model, effort or activity.
        if record.get("isApiErrorMessage") or (
                isinstance(message, dict) and str(message.get("model", "")).startswith("<")):
            continue
        if not newest:
            newest = True
            if isinstance(message, dict):
                model = _model_short(message.get("model"))
            eff = record.get("effort")
            effort = eff if isinstance(eff, str) else None
        if desc is None:
            blocks = message.get("content") if isinstance(message, dict) else None
            if isinstance(blocks, list):
                d = _describe_blocks(blocks)
                if d:
                    desc = _truncate(_single(d), max_len)
    return desc, model, effort


def describe(transcript: Path, max_len: int = 60) -> str | None:
    """A single-line description of the newest concrete assistant turn in `transcript`, or None when the file
    is missing/unreadable or no suitable record is found."""
    return scan(transcript, max_len)[0]


def describe_config(transcript: Path) -> tuple[str | None, str | None]:
    """(model_short, effort) for the newest non-sidechain assistant record in `transcript`."""
    return scan(transcript)[1:]
