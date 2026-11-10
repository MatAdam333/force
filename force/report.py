"""Reported state (SPEC §43.2): a script tells Force what a session is doing (`force report`), so a long build or
a render in a shell session shows as a working card, and its end as "your turn".

One small JSON file per session in $FORCE_STATE/reports/<name>.json: {"state", "summary", "pid", "at_ms"}. The
daemon applies and expires them; this module only reads and writes the files."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import Paths

# what a report may say; "idle" is not stored: reporting it clears the report
STATES = ("working", "needs-you", "your-turn")
SUMMARY_MAX = 80


@dataclass(frozen=True)
class Report:
    state: str              # one of STATES
    summary: str | None     # one line, shown on the card
    pid: int                # the reporting process: working/needs-you hold only while it lives; 0 = no owner
    at_ms: int              # wall clock of the report


def reports_dir(paths: Paths) -> Path:
    return paths.state / "reports"


def path_for(paths: Paths, name: str) -> Path:
    return reports_dir(paths) / f"{name}.json"


def clean_summary(text: str | None) -> str | None:
    """One line of at most SUMMARY_MAX characters (control characters dropped), None when empty."""
    if not text:
        return None
    s = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return s[:SUMMARY_MAX] or None


def write(paths: Paths, name: str, state: str, summary: str | None, pid: int, now_ms: int) -> None:
    """Store a report (atomic). `state` must be in STATES."""
    if state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    p = path_for(paths, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"state": state, "summary": clean_summary(summary), "pid": int(pid), "at_ms": int(now_ms)}
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, p)


def clear(paths: Paths, name: str) -> None:
    try:
        path_for(paths, name).unlink()
    except FileNotFoundError:
        pass


def read(paths: Paths, name: str) -> Report | None:
    """The stored report, or None when missing or unreadable (a bad file is not an error: nothing is shown)."""
    try:
        d = json.loads(path_for(paths, name).read_text())
        if not isinstance(d, dict) or d.get("state") not in STATES:
            return None
        summary = d.get("summary")
        return Report(state=d["state"], summary=summary if isinstance(summary, str) else None,
                      pid=int(d.get("pid") or 0), at_ms=int(d.get("at_ms") or 0))
    except (OSError, ValueError, TypeError):
        return None


def names(paths: Paths) -> list[str]:
    """Session names with a report file (readable or not)."""
    try:
        return sorted(p.name[:-5] for p in reports_dir(paths).iterdir() if p.name.endswith(".json"))
    except OSError:
        return []


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
