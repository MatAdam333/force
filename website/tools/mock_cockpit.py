#!/usr/bin/env python3
"""Design mock-up of a refined Force cockpit: the agent pane is the real captured screen, the sidebar and header are
redrawn the way the redesign would draw them. For deciding the redesign before it is built.

    mock_cockpit.py IN.ansi OUT.html [--logo pixel|text]
"""
import html
import os
import re
import socket
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from ansi2html import CHAR_MAP, DEFAULT_BG, DEFAULT_FG, OTHER_ESC, SGR, Style  # noqa: E402

# the machine the screens were captured on; the published screens call it "devbox" (same width, so nothing moves)
CAPTURE_HOST = os.environ.get("FORCE_CAPTURE_HOST") or socket.gethostname().split(".")[0]


def neutral(text: str) -> str:
    return text.replace(CAPTURE_HOST, "devbox".ljust(len(CAPTURE_HOST)))

COLS, ROWS, SB = 150, 40, 29


def parse(path):
    """The captured screen as a grid of [char, css-key] cells."""
    text = OTHER_ESC.sub("", open(path, encoding="utf-8").read()).translate(CHAR_MAP)
    grid = []
    st = Style()   # the SGR state carries over line ends, as in a terminal (tmux capture-pane relies on it)
    for line in text.split("\n")[:ROWS]:
        row, pos = [], 0
        for m in SGR.finditer(line):
            for ch in line[pos:m.start()]:
                row.append([ch, st.css()])
            params = [int(x) if x else 0 for x in re.split("[;:]", m.group(1))] if m.group(1) else [0]
            st.apply(params)
            pos = m.end()
        for ch in line[pos:]:
            row.append([ch, st.css()])
        row += [[" ", ""]] * (COLS - len(row))
        grid.append(row[:COLS])
    while len(grid) < ROWS:
        grid.append([[" ", ""] for _ in range(COLS)])
    return grid


def put(grid, r, c, text, css="", width=None):
    for i, ch in enumerate(text):
        if c + i < COLS and (width is None or i < width):
            grid[r][c + i] = [ch, css]


def fill(grid, r, c0, c1, css):
    for c in range(c0, c1):
        grid[r][c] = [" ", css]


BG_SEL, BG_NEED = "background:#313131", "background:#3b2626"
WHITE, DIM, DIMMER = "color:#f2f2f2;font-weight:700", "color:#8c8c8c", "color:#5f5f5f"
RED, GREEN, YELLOW, GREY = "color:#ff6b6b", "color:#7dd87d", "color:#e5c07b", "color:#a3a3a3"
CORAL, PEACH = "color:#d97757", "color:#f6c2a4"


def sidebar(grid, logo, old):
    for r in range(ROWS):
        fill(grid, r, 0, SB, "")
    top = 1
    if logo == "pixel":
        for r in range(0, 6):                       # the existing pixel wordmark + underline + name line, one row lower
            grid[r + 1][:SB] = [cell[:] for cell in old[r][:SB]]
        top = 9
    else:
        word = "F O R C E"
        grad = ["#d97757", "#e08a6b", "#e89d80", "#efb093", "#f6c2a4"]
        for i, ch in enumerate(word):
            if ch != " ":
                put(grid, 2, 2 + i, ch, f"color:{grad[i // 2]};font-weight:700;font-style:italic")
        put(grid, 3, 2, "━" * 9, "color:#a8553a")
        put(grid, 5, 2, "The code cockpit", DIM)
        top = 8
    cards = [
        ("1", "$", "shell", "3h", "shell ~", GREY, None),
        ("2", "○", "api", "1m", "idle", GREY, "sel"),
        ("3", "!", "billing-fix", "1m", "needs permission", RED, "need"),
        ("4", "●", "docs-site", "6m", "your turn", GREEN, None),
        ("5", "○", "infra", "42m", "idle", GREY, None),
        ("6", "◐", "mobile", "1m", "running a command", YELLOW, None),
        ("7", "◌", "migration-2024", "30h", "stale", "color:#707070", None),
    ]
    r = top
    for idx, glyph, name, age, status, gcss, kind in cards:
        rows = 3 if kind == "sel" else 2
        bg = BG_SEL if kind == "sel" else ""
        for k in range(rows):
            fill(grid, r + k, 0, SB, bg)
        edge = {"sel": CORAL, "need": RED}.get(kind)
        if edge:
            for k in range(rows):
                put(grid, r + k, 0, "▎", f"{edge};{bg}")
        put(grid, r, 2, idx, f"{DIMMER};{bg}")
        put(grid, r, 4, glyph, f"{gcss};font-weight:700;{bg}")
        put(grid, r, 6, name, f"{WHITE if kind == 'sel' else 'color:#dedede'};{bg}")
        put(grid, r, SB - 1 - len(age), age, f"{DIM};{bg}")
        scss = RED if kind == "need" else (GREEN if gcss == GREEN else (YELLOW if gcss == YELLOW else DIM))
        put(grid, r + 1, 6, status, f"{scss};{bg}")
        if kind == "sel":
            put(grid, r + 2, 6, "sonnet 4.6 · high", f"{DIMMER};{bg}")
            put(grid, r + 2, SB - 2, "A", f"{GREEN};{bg}")
        r += rows + 1
    put(grid, r + 1, 2, "+ new", DIM)
    put(grid, r + 1, 16, "archived 0", DIMMER)
    put(grid, ROWS - 5, 2, "5h", DIM); put(grid, ROWS - 5, 6, "━━━━╸", PEACH); put(grid, ROWS - 5, 11, "━━━━━━━━", "color:#3a3a3a")
    put(grid, ROWS - 5, 21, "34%", DIM)
    put(grid, ROWS - 4, 2, "wk", DIM); put(grid, ROWS - 4, 6, "━━╸", PEACH); put(grid, ROWS - 4, 9, "━━━━━━━━━━", "color:#3a3a3a")
    put(grid, ROWS - 4, 21, "18%", DIM)
    put(grid, ROWS - 2, 2, "●", GREEN); put(grid, ROWS - 2, 4, "devbox", DIM)


def header(grid):
    for r in (0, 1):
        fill(grid, r, SB + 1, COLS, "")
    x = SB + 3
    put(grid, 1, x, "≡", DIM); put(grid, 1, x + 2, "sidebar", "color:#bdbdbd"); x += 13
    def chip(x, glyph, name, note, gcss, bg):
        fill(grid, 1, x, x + len(name) + len(note) + 6, bg)
        put(grid, 1, x + 1, glyph, f"{gcss};font-weight:700;{bg}")
        put(grid, 1, x + 3, name, f"color:#f2f2f2;font-weight:700;{bg}")
        put(grid, 1, x + 4 + len(name), note, f"{DIM};{bg}")
        return x + len(name) + len(note) + 8
    x = chip(x, "!", "billing-fix", "permission", RED, "background:#3b2626")
    x = chip(x, "●", "docs-site", "finished", GREEN, "background:#25332a")
    right = "◐ 1 working    ? controls    ✕ quit"
    rx = COLS - 2 - len(right)
    put(grid, 1, rx, "◐", YELLOW); put(grid, 1, rx + 2, "1 working", YELLOW)
    put(grid, 1, rx + 15, "?", DIM); put(grid, 1, rx + 17, "controls", "color:#bdbdbd")
    put(grid, 1, rx + 29, "✕", DIM); put(grid, 1, rx + 31, "quit", "color:#bdbdbd")


def render(grid):
    out = []
    for row in grid:
        parts, cur, buf = [], None, []
        for ch, css in row:
            if css != cur:
                if buf:
                    t = html.escape("".join(buf))
                    parts.append(f'<span style="{cur}">{t}</span>' if cur else t)
                cur, buf = css, []
            buf.append(ch)
        if buf:
            t = html.escape("".join(buf))
            parts.append(f'<span style="{cur}">{t}</span>' if cur else t)
        out.append("".join(parts))
    return "\n".join(out)


def main(argv):
    src, dst = argv[0], argv[1]
    logo = argv[3] if len(argv) > 3 and argv[2] == "--logo" else "pixel"
    old = parse(src)
    grid = [[c[:] for c in row] for row in old]
    sidebar(grid, logo, old)
    header(grid)
    text = neutral(render(grid))
    open(dst, "w", encoding="utf-8").write(text)


if __name__ == "__main__":
    main(sys.argv[1:])


# --- the bolder variant: gradients where they carry meaning ----------------------------------------------
def _hex(c):
    return [int(c[i:i + 2], 16) for i in (1, 3, 5)]


def grad(a, b, k):
    x, y = _hex(a), _hex(b)
    return "#%02x%02x%02x" % tuple(round(x[i] + (y[i] - x[i]) * k) for i in range(3))


def grad_fill(grid, r, c0, c1, a, b, keep_fg=True):
    """A horizontal background gradient a -> b over columns c0..c1 (text kept)."""
    n = max(1, c1 - c0 - 1)
    for c in range(c0, c1):
        ch, css = grid[r][c]
        css = re.sub(r"background:[^;]*;?", "", css or "")
        grid[r][c] = [ch, f"{css};background:{grad(a, b, (c - c0) / n)}".strip(";")]


def bolder(grid):
    # selected card (api, 3 rows) and the needs-you card (billing-fix, 2 rows): find them by their names
    for r in range(ROWS):
        line = "".join(ch for ch, _ in grid[r][:SB])
        if " api " in line + " ":
            for k in range(3):
                grad_fill(grid, r + k, 0, SB, "#4a3027", "#262626")
                put(grid, r + k, 0, "▌", "color:#d97757;background:#4a3027")
        if "billing-fix" in line:
            for k in range(2):
                grad_fill(grid, r + k, 0, SB, "#3f2424", "#262626")
                put(grid, r + k, 0, "▌", "color:#ff6b6b;background:#3f2424")
        if line.strip().startswith(("5h", "wk")):
            # usage bar: green -> amber along its filled part
            filled = [c for c in range(6, 20) if grid[r][c][0] in "━╸" and "#3a3a3a" not in grid[r][c][1]]
            for i, c in enumerate(filled):
                grid[r][c][1] = f"color:{grad('#7dd87d', '#e5c07b', i / max(1, len(filled) - 1))}"
    # logo underline: coral fading out
    for r in range(ROWS):
        if all(grid[r][c][0] == "▔" for c in range(0, 10)):
            for c in range(SB):
                if grid[r][c][0] == "▔":
                    grid[r][c][1] = f"color:{grad('#d97757', '#262626', c / SB)}"
    # header pills: tinted gradients
    row = 1
    line = "".join(ch for ch, _ in grid[row])
    for name, a in (("billing-fix", "#4a2626"), ("docs-site", "#253a2b")):
        i = line.find(name)
        if i > 0:
            start = i - 4
            end = line.find("  ", i + len(name) + 2)
            end = end if end > 0 else i + len(name) + 14
            grad_fill(grid, row, start, end + 2, a, "#262626")


def main2(argv):
    src, dst = argv[0], argv[1]
    old = parse(src)
    grid = [[c[:] for c in row] for row in old]
    sidebar(grid, "pixel", old)
    header(grid)
    bolder(grid)
    open(dst, "w", encoding="utf-8").write(neutral(render(grid)))


# --- the "tall" variant: the technique Textual uses ---------------------------------------------------------
# Surfaces are background colours, and their edges are drawn with the 1/8 block characters (▁ ▔) and half blocks
# (▐ ▌) in the surface colour over the parent colour. A filled area then ends a fraction of a cell inside its row
# and column, so it reads as an inset card or pill with padding and soft corners, not as a run of whole cells.
CHROME = "#1b1b1b"          # sidebar + header: the app shell around the agent pane
LINE = "#2c2c2c"


def surface(grid, r0, r1, c0, c1, bg, parent=CHROME):
    """A filled box over rows r0..r1-1, cols c0..c1-1 whose edges sit inside the outer cells: ▁ row above,
    ▔ row below, ▐ / ▌ half cells left and right. Text goes on rows r0.., cols c0+1..c1-2."""
    for r in range(r0, r1):
        fill(grid, r, c0 + 1, c1 - 1, f"background:{bg}")
        grid[r][c0] = ["▐", f"color:{bg};background:{parent}"]
        grid[r][c1 - 1] = ["▌", f"color:{bg};background:{parent}"]
    for c in range(c0 + 1, c1 - 1):
        if r0 > 0:
            grid[r0 - 1][c] = ["▁", f"color:{bg};background:{parent}"]
        if r1 < ROWS:
            grid[r1][c] = ["▔", f"color:{bg};background:{parent}"]


def tall(grid, old):
    ch = f"background:{CHROME}"
    for r in range(ROWS):
        fill(grid, r, 0, SB + 1, ch)
    for r in range(3):
        fill(grid, r, SB, COLS, ch)
    for r in range(0, 6):                                   # pixel wordmark, one row lower, on the shell colour
        for c in range(SB):
            cell, css = old[r][c]
            css = css or ""
            grid[r + 1][c] = [cell, css if "background" in css else f"{css};{ch}".strip(";")]
        grid[r + 1][0] = [" ", ch]                          # col 0 holds a stitching remnant of the capture
    cards = [
        ("$", "shell", "3h", "shell ~", GREY, None),
        ("○", "api", "1m", "idle", GREY, "sel"),
        ("!", "billing-fix", "1m", "needs permission", RED, "need"),
        ("●", "docs-site", "6m", "your turn", GREEN, None),
        ("○", "infra", "42m", "idle", GREY, None),
        ("◐", "mobile", "1m", "running a command", YELLOW, None),
        ("◌", "migration-2024", "30h", "stale", "color:#707070", None),
    ]
    r = 9
    for glyph, name, age, status, gcss, kind in cards:
        rows = 3 if kind == "sel" else 2
        bg = {"sel": "#3a2d27", "need": "#362424"}.get(kind, CHROME)
        if kind:
            surface(grid, r, r + rows, 1, SB - 1, bg)
        b = f"background:{bg}"
        put(grid, r, 3, glyph, f"{gcss};font-weight:700;{b}")
        put(grid, r, 5, name, f"{'color:#ffffff;font-weight:700' if kind else 'color:#d6d6d6'};{b}")
        put(grid, r, SB - 3 - len(age), age, f"{DIMMER if not kind else DIM};{b}")
        scss = {"need": RED}.get(kind) or (gcss if gcss in (GREEN, YELLOW) else DIM)
        put(grid, r + 1, 5, status, f"{scss};{b}")
        if kind == "sel":
            put(grid, r + 2, 5, "sonnet 4.6 · high", f"{DIMMER};{b}")
        r += rows + 1
    # new session: a ghost button in the same shape
    surface(grid, r + 1, r + 2, 1, 12, "#262626")
    put(grid, r + 1, 3, "+ new", f"color:#bdbdbd;background:#262626")
    put(grid, r + 1, 15, "archived 0", f"{DIMMER};{ch}")
    # usage meters
    for rr, label, frac, pct in ((ROWS - 6, "5h", .34, "34%"), (ROWS - 4, "wk", .18, "18%")):
        put(grid, rr, 3, label, f"{DIM};{ch}")
        n, w = round(16 * frac), 16
        for i in range(w):
            col = grad("#7dd87d", "#e5c07b", i / max(1, n - 1)) if i < n else "#333333"
            put(grid, rr, 7 + i, "━", f"color:{col};{ch}")
        put(grid, rr, SB - 2 - len(pct), pct, f"{DIM};{ch}")
    put(grid, ROWS - 2, 3, "●", f"{GREEN};{ch}"); put(grid, ROWS - 2, 5, "devbox", f"{DIM};{ch}")

    # header: tall pills on the shell colour, no rule underneath: the pane's own colour is the edge
    x = SB + 2
    put(grid, 1, x, "≡", f"{DIM};{ch}")
    x += 3

    def pill(x, glyph, name, note, gcss, bg, fg="#ffffff"):
        w = len(name) + len(note) + 8
        surface(grid, 1, 2, x, x + w, bg)
        b = f"background:{bg}"
        put(grid, 1, x + 2, glyph, f"{gcss};font-weight:700;{b}")
        put(grid, 1, x + 4, name, f"color:{fg};font-weight:700;{b}")
        put(grid, 1, x + 5 + len(name), note, f"{DIM};{b}")
        return x + w + 1

    x = pill(x, "!", "billing-fix", "permission", RED, "#3e2525")
    x = pill(x, "●", "docs-site", "finished", GREEN, "#24352a")
    rx = COLS - 40
    put(grid, 1, rx, "◐", f"{YELLOW};{ch}"); put(grid, 1, rx + 2, "1 working", f"{YELLOW};{ch}")
    rx = pill(rx + 13, "?", "controls", "", DIM, "#2a2a2a", "#d6d6d6")
    put(grid, 1, rx + 1, "✕", f"{DIM};{ch}"); put(grid, 1, rx + 3, "quit", f"color:#bdbdbd;{ch}")


def main3(argv):
    src, dst = argv[0], argv[1]
    old = parse(src)
    grid = [[c[:] for c in row] for row in old]
    tall(grid, old)
    open(dst, "w", encoding="utf-8").write(neutral(render(grid)))
