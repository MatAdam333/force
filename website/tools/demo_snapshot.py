#!/usr/bin/env python3
"""A made-up Force snapshot for the landing page's cockpit picture and the brag video: invented sessions in every
state, so the real sidebar and header draw them. Usage: demo_snapshot.py OUT.json [SHOWN]  (SHOWN: the session on
screen, default api)"""
import json, os, sys, time, uuid

now = int(time.time() * 1000)
SHOWN = sys.argv[2] if len(sys.argv) > 2 else "api"
def s(name, idx, state, ago_min, activity=None, model="sonnet 4.6", effort="high", mode="auto", kind="claude",
      viewed=False, waiting=None):
    return {"name": name, "index": idx, "state": state, "since_ms": now - ago_min * 60_000, "version": "2.1.318",
            "outdated": False, "archived": False, "order": idx, "cwd": os.path.expanduser("~") if kind == "shell" else f"/home/dev/{name}",
            "session_id": str(uuid.uuid5(uuid.NAMESPACE_URL, name)), "pid": 1000 + idx, "waiting_for": waiting,
            "viewed": viewed, "activity": activity, "mode": mode if kind == "claude" else None,
            "model": model if kind == "claude" else None, "effort": effort if kind == "claude" else None,
            "shared": None, "note": None, "kind": kind, "why": None, "title": None}

sessions = [
    s("shell", 1, "SHELL", 180, activity="shell ~", kind="shell"),
    s("api", 2, "IDLE", 1, activity="idle", mode="edits", viewed=SHOWN == "api"),
    s("billing-fix", 3, "NEEDS_YOU", 1, activity="allow Bash: npm test?", waiting="permission", mode="manual",
      viewed=SHOWN == "billing-fix"),
    s("docs-site", 4, "YOUR_TURN", 6, activity="your turn", model="opus 5.5"),
    s("infra", 5, "IDLE", 42, activity="idle", effort="medium"),
    s("mobile", 6, "WORKING", 1, activity="running a command", kind="claude", model="opus 5.5", mode="edits"),
    s("migration-2024", 7, "STALE", 60 * 30, activity="stale"),
]
for x in sessions:   # the session on screen is the one you are looking at
    x["viewed"] = x["name"] == SHOWN
snap = {"generated_ms": now, "latest_version": "2.1.318",
        "counts": {"needs_you": 1, "working": 2, "your_turn": 1, "idle": 1, "stale": 1, "down": 0},
        "smoke": {"ok": True, "at": now / 1000}, "sessions": sessions,
        "usage": {"five_hour": {"used_percentage": 34.0, "resets_at": int(time.time()) + 3 * 3600},
                  "seven_day": {"used_percentage": 18.0, "resets_at": int(time.time()) + 4 * 86400}}}
json.dump(snap, open(sys.argv[1], "w"), indent=1)
