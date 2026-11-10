"""Plays force-intro on a pty, then closes the pty's master after 2 s (the terminal hangs up) and
reports how the intro exited (expected: 129 = 128 + SIGHUP, within ~0.1 s).

    python3 tools/dev/hangup.py BIN close [STDERR_FILE] [intro args...]
"""
import os, pty, time, sys, select, signal
binp = sys.argv[1]; how = sys.argv[2]
pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = "xterm-256color"
    errf = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644) if len(sys.argv) > 3 else None
    if errf is not None: os.dup2(errf, 2)
    os.execv(binp, [binp, "--color", "truecolor"] + sys.argv[4:])
import fcntl, termios, struct
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
t0 = time.time()
while time.time() - t0 < 2:
    r, _, _ = select.select([fd], [], [], 0.05)
    if r:
        data = os.read(fd, 65536)
        os.write(fd, b"\x1b[?62c" * data.count(b"\x1b[c"))
if how == "close":
    os.close(fd)            # the terminal goes away (master closed: hangup)
else:
    signal.signal(signal.SIGALRM, lambda *a: None)
t1 = time.time()
for _ in range(60):
    p, st = os.waitpid(pid, os.WNOHANG)
    if p:
        print(f"{how}: exited {os.waitstatus_to_exitcode(st)} after {time.time()-t1:.2f} s"); break
    time.sleep(0.1)
else:
    print(f"{how}: STILL RUNNING after 6 s"); os.kill(pid, 9)
