#!/usr/bin/env python3
"""Fake claude used as FORCE_CLAUDE_BIN in integration tests (SPEC §13).
Writes sessions/<pid>.json, reacts to a control file, removes its json on SIGTERM."""
import json
import os
import signal
import sys
import time
import uuid


def main(argv):
    argv = argv[1:]
    session_id = None
    name = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--session-id", "--resume"):
            i += 1
            if i < len(argv):
                session_id = argv[i]
        elif a == "-n":
            i += 1
            if i < len(argv):
                name = argv[i]
        i += 1

    claude_dir = os.environ.get("FORCE_CLAUDE_DIR")
    sid = session_id or str(uuid.uuid4())
    pid = os.getpid()
    sessions = os.path.join(claude_dir, "sessions")
    projects = os.path.join(claude_dir, "projects", "-tmp")
    os.makedirs(sessions, exist_ok=True)
    os.makedirs(projects, exist_ok=True)
    cwd = os.getcwd()

    transcript = os.path.join(projects, f"{sid}.jsonl")
    if not os.path.exists(transcript):
        with open(transcript, "w") as f:
            f.write(json.dumps({"sessionId": sid}) + "\n")

    status_path = os.path.join(sessions, f"{pid}.json")
    ctl_path = os.path.join(sessions, f"{pid}.ctl")
    current_session = sid

    def write_status(status="idle", sessionId=None, nm=None, waiting_for=None):
        data = {
            "pid": pid,
            "sessionId": sessionId if sessionId is not None else current_session,
            "cwd": cwd,
            "version": "9.9.9",
            "name": nm if nm is not None else name,
            "nameSource": "user",
            "status": status,
            "statusUpdatedAt": int(time.time() * 1000),
        }
        if waiting_for:
            data["waitingFor"] = waiting_for
        with open(status_path, "w") as f:
            json.dump(data, f)

    write_status()

    def on_term(_signum, _frame):
        try:
            os.unlink(status_path)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)

    while True:
        if os.path.exists(ctl_path):
            ctl = None
            try:
                with open(ctl_path) as f:
                    ctl = json.load(f)
            except Exception:
                ctl = None
            if isinstance(ctl, dict):
                if "status" in ctl:
                    write_status(status=ctl["status"], waiting_for=ctl.get("waitingFor"))
                if "sessionId" in ctl:
                    current_session = ctl["sessionId"]
                    write_status(status="idle")
                if "name" in ctl:
                    write_status(status="idle", nm=ctl["name"])
                if "exit" in ctl:
                    try:
                        os.unlink(status_path)
                    except OSError:
                        pass
                    sys.exit(ctl["exit"])
            try:
                os.unlink(ctl_path)
            except OSError:
                pass
        time.sleep(0.1)


if __name__ == "__main__":
    main(sys.argv)
