"""Runs force-intro on a pty and plays a terminal: reads its output at a limited rate, answers the
DA1 queries (ESC [ c) when it reaches them (optionally after a delay), or never.

    python3 tools/dev/fake_term.py [--rate BYTES_PER_S] [--delay MS] [--mute] [--size 120x40]
        [--px 1200x840] [--key-at SECONDS] [--dump STREAM.bin] BIN [-- intro args]

--rate limits how fast the "terminal" reads (a slow link or terminal; the pty buffer fills and the
intro's flow control sees it), --delay holds each answer back (round trip), --mute never answers,
--px sets the window's pixel size (cell aspect is measured from it), --key-at presses q, --dump
writes the raw output stream (tools/dev/emu.py replays it). The intro runs with --verbose; its
summary line is printed last.
Prints wall time, bytes, frames seen, answers and stderr/stats lines.
"""
import argparse, os, pty, select, struct, fcntl, termios, time, sys, re

ap = argparse.ArgumentParser()
ap.add_argument("bin")
ap.add_argument("--rate", type=float, default=0)  # 0 = unlimited
ap.add_argument("--delay", type=float, default=0)  # ms before an answer leaves
ap.add_argument("--mute", action="store_true")
ap.add_argument("--size", default="120x40")
ap.add_argument("--key-at", type=float, default=0)  # press 'q' at this time
ap.add_argument("--px", default="0x0")  # the window's size in pixels (TIOCGWINSZ)
ap.add_argument("--dump", default="")  # write the raw output stream here
ap.add_argument("rest", nargs="*")
a = ap.parse_args()
cols, rows = map(int, a.size.split("x"))

pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = "xterm-256color"
    os.environ["LANG"] = "C.UTF-8"
    os.execv(a.bin, [a.bin, "--color", "truecolor", "--verbose"] + a.rest)
xp, yp = map(int, a.px.split("x"))
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, xp, yp))

t0 = time.monotonic()
dumpf = open(a.dump, "wb") if a.dump else None
budget_t = t0
total = 0
frames = 0
answers_due = []  # times at which to send an answer
sent = 0
tail = b""
out_after_restore = b""
key_sent = False
first_frame = None
while True:
    now = time.monotonic()
    while answers_due and answers_due[0] <= now:
        answers_due.pop(0)
        try:
            os.write(fd, b"\x1b[?62;22c")
            sent += 1
        except OSError:
            pass
    if a.key_at and not key_sent and now - t0 >= a.key_at:
        os.write(fd, b"q")
        key_sent = True
    timeout = 0.005
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        continue
    chunk = 65536
    if a.rate:
        # read only what the budget allows
        allowed = int((now - budget_t) * a.rate)
        if allowed <= 0:
            time.sleep(0.002)
            continue
        chunk = min(chunk, allowed)
    try:
        data = os.read(fd, chunk)
    except OSError:
        break
    if not data:
        break
    if a.rate:
        budget_t = max(budget_t, now - 0.05) + len(data) / a.rate
    total += len(data)
    if a.dump:
        dumpf.write(data)
    buf = tail + data
    new = lambda pat: buf.count(pat) - tail.count(pat)
    n_frames = new(b"\x1b[?2026h")
    frames += n_frames
    if n_frames and first_frame is None:
        first_frame = now - t0
    if not a.mute:
        for _ in range(new(b"\x1b[c")):
            answers_due.append(now + a.delay / 1000)
    tail = buf[-7:]
    out_after_restore = (out_after_restore + data)[-4096:]
_, status = os.waitpid(pid, 0)
dt = time.monotonic() - t0
print(f"wall {dt:.2f}s (first frame at {first_frame}), {total/1e6:.2f} MB, {frames} frames seen, "
      f"{sent} answers sent, exit {os.waitstatus_to_exitcode(status)}")
m = re.search(rb"force-intro: [^\r\n]*", out_after_restore)
print(m.group(0).decode() if m else "(no stats)")
