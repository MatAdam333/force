#!/usr/bin/env python3
"""A Force screen (coloured text captured from the terminal, cockpit-*.html / menu-*.html) as the page draws it.

A terminal paints every cell's background first and the text over it, and draws block characters itself. The page
does the same: the screen is laid out on its cell grid here, and every background and block character becomes a box
in one paint layer under the text (<span class="paint">, empty, so the screen's text stays exactly what was
captured). Runs of one colour are one box; a bar running down several rows is one box. Boxes reach a pixel into the
next cell and line, which the next box paints over, so no seam opens at any zoom; the text is plain inline text on
top, never cut (underscores reach below their line) and never nudged off the grid. style.css places the boxes.

    screen.py FILE [--overlay]      print FILE as drawn (--overlay: a menu laid over a screen, no default fill)
"""
import re
import sys
from html.parser import HTMLParser

DEFAULT_BG = "#212121"      # Force's background: the cells a screen leaves uncoloured

# block characters as boxes (row offset, column offset, width, height) in cells
_Q = {"ul": (0, 0, .5, .5), "ur": (0, .5, .5, .5), "ll": (.5, 0, .5, .5), "lr": (.5, .5, .5, .5)}
_QUAD = {"▀": "ul ur", "▄": "ll lr", "▌": "ul ll", "▐": "ur lr", "█": "ul ur ll lr",
         "▖": "ll", "▗": "lr", "▘": "ul", "▝": "ur", "▙": "ul ll lr", "▚": "ul lr",
         "▛": "ul ur ll", "▜": "ul ur lr", "▞": "ur ll", "▟": "ur ll lr"}


def _shapes(ch: str) -> list[tuple[float, float, float, float]] | None:
    c = ord(ch)
    if ch == "█":
        return [(0, 0, 1, 1)]
    if ch in ("▀", "▄", "▌", "▐"):          # halves: one box, not two quarters
        return {"▀": [(0, 0, 1, .5)], "▄": [(.5, 0, 1, .5)],
                "▌": [(0, 0, .5, 1)], "▐": [(0, .5, .5, 1)]}[ch]
    if ch in _QUAD:
        return [_Q[q] for q in _QUAD[ch].split()]
    if 0x2589 <= c <= 0x258F:                                   # left n/8 (U+258D is the session's status bar)
        return [(0, 0, (0x2590 - c) / 8, 1)]
    if 0x2581 <= c <= 0x2587:                                   # lower n/8
        n = (c - 0x2580) / 8
        return [(1 - n, 0, 1, n)]
    if c == 0x2594:
        return [(0, 0, 1, 1 / 8)]
    if c == 0x2595:
        return [(0, 7 / 8, 1 / 8, 1)]
    return None                                                 # shades (U+2591-2593) stay glyphs


# box drawing as lines on the cell's centre lines, DejaVu Sans Mono's own strokes (light horizontal 172/2048 em,
# light vertical 160/2048, heavy 344/2048): ("h"|"v", start, end, thickness), start/end in cells along the line
_LH, _LV, _HH = "0.083984em", "0.078125em", "0.167969em"
_LINES = {
    "\u2500": [("h", 0, 1, _LH)], "\u2501": [("h", 0, 1, _HH)], "\u2502": [("v", 0, 1, _LV)],
    "\u254c": [("h", 60 / 1233, 1173 / 1233, _LH)],                       # light double dash: one dash per cell
    "\u2578": [("h", 0, .5, _HH)],
    "\u251c": [("v", 0, 1, _LV), ("h", .5, 1, _LH)], "\u2524": [("v", 0, 1, _LV), ("h", 0, .5, _LH)],
    "\u2514": [("v", 0, .5, _LV), ("h", .5, 1, _LH)], "\u250c": [("v", .5, 1, _LV), ("h", .5, 1, _LH)],
    "\u2510": [("v", .5, 1, _LV), ("h", 0, .5, _LH)], "\u2518": [("v", 0, .5, _LV), ("h", 0, .5, _LH)],
}
# rounded corners: which way the arc leaves the centre (horizontal side, vertical side)
_ARCS = {"\u256d": ("right", "down"), "\u256e": ("left", "down"), "\u256f": ("left", "up"), "\u2570": ("right", "up")}


class _Grid(HTMLParser):
    """Walks the screen: every character's row, column, colour and background."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.row = self.col = 0
        self.cells: dict[tuple[int, int], tuple[str | None, str | None, str]] = {}   # (row, col) -> (bg, fg, ch)
        self.width = 0

    def handle_starttag(self, tag, attrs):
        st = dict(attrs).get("style") or ""
        bg = re.search(r"background:\s*(#[0-9a-fA-F]{3,8})", st)
        fg = re.search(r"(?<![-\w])color:\s*(#[0-9a-fA-F]{3,8})", st)
        top = self.stack[-1] if self.stack else {"bg": None, "fg": None}
        self.stack.append({"bg": bg.group(1).lower() if bg else top["bg"], "fg": fg.group(1).lower() if fg else top["fg"]})

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        top = self.stack[-1] if self.stack else {"bg": None, "fg": None}
        for ch in data:
            if ch == "\n":
                self.row += 1
                self.col = 0
                continue
            self.cells[(self.row, self.col)] = (top["bg"], top["fg"], ch)
            self.col += 1
            self.width = max(self.width, self.col)


def _box(r, c, n, h, color, e=(0, 1, 1, 0), t: str | None = None, along: str = "") -> str:
    """One box at row r, column c, n cells wide, h rows high; e = (top, right, bottom, left) reach in px. A line
    (t: its thickness, along "h" or "v") is centred on its row or column line and t thick."""
    num = lambda v: f"{v:.4f}".rstrip("0").rstrip(".")
    st = f"--r:{num(r)};--c:{num(c)};--n:{num(n)};--h:{num(h)};--bg:{color}"
    if t:
        st += f";--t:{t}" + (";--ty:-.5;--th:1" if along == "h" else ";--tx:-.5;--tw:1")
    if e != (0, 1, 1, 0):
        st += "".join(f";--e{k}:{num(x)}px" for k, x in zip("trbl", e))
    return f'<i style="{st}"></i>'


def render(src: str, overlay: bool = False) -> str:
    g = _Grid()
    g.feed(src)
    rows = g.row + 1
    boxes: list[str] = []
    # backgrounds: one box per run of a colour in a row; a screen's uncoloured cells are Force's background, so
    # every box's reach into the next line lands on a box of that line (an overlay leaves them clear)
    for r in range(rows):
        run_c, run_color = 0, None
        for c in range(g.width + 1):
            cell = g.cells.get((r, c))
            color = cell[0] if cell and cell[0] else (None if overlay else DEFAULT_BG)
            if c == g.width:
                color = "end"
            if color != run_color:
                if run_color and run_color != "end":
                    boxes.append(_box(r, run_c, c - run_c, 1, run_color))
                run_c, run_color = c, color
    # block characters: shapes in cells, merged across a row and down a column, then drawn over the backgrounds
    shapes: list[list] = []          # [r, c, n, h, color]
    for (r, c), (_, fg, ch) in sorted(g.cells.items()):
        for dr, dc, w, h in _shapes(ch) or []:
            shapes.append([r + dr, c + dc, w, h, fg or "#d0d0d0"])
    merged: list[list] = []
    for s in sorted(shapes, key=lambda s: (s[0], s[3], s[4], s[1])):            # across: same row, height, colour
        m = merged[-1] if merged else None
        if m and m[0] == s[0] and m[3] == s[3] and m[4] == s[4] and abs(m[1] + m[2] - s[1]) < 1e-9:
            m[2] += s[2]
        else:
            merged.append(list(s))
    down: list[list] = []
    for s in sorted(merged, key=lambda s: (s[1], s[2], s[4], s[0])):            # down: same column, width, colour
        m = down[-1] if down else None
        if m and m[1] == s[1] and m[2] == s[2] and m[4] == s[4] and abs(m[0] + m[3] - s[0]) < 1e-9:
            m[3] += s[3]
        else:
            down.append(list(s))
    for r, c, n, h, color in sorted(down, key=lambda s: (s[0], s[1])):
        # reach half a pixel past every edge that lies on a cell boundary (soft edges of neighbouring shapes never
        # leave a hairline between them); a thin bar keeps its width
        on = lambda v: abs(v - round(v)) < 1e-9
        side = .5 if n >= .5 else 0
        e = (.5 if on(r) else 0, side if on(c + n) else 0, .5 if on(r + h) else 0, side if on(c) else 0)
        boxes.append(_box(r, c, n, h, color, e))
    # box drawing: segments on the centre lines, merged along a row and down a column; the rounded corners as arcs
    segs: list[list] = []            # [kind, fixed (row or col centre), start, end, t, color]
    arcs: list[str] = []
    for (r, c), (_, fg, ch) in sorted(g.cells.items()):
        color = fg or "#d0d0d0"
        for kind, a, b, t in _LINES.get(ch, []):
            segs.append(["h", r + .5, c + a, c + b, t, color] if kind == "h" else ["v", c + .5, r + a, r + b, t, color])
        if ch in _ARCS:
            hs, vs = _ARCS[ch]
            # a box from the cell's centre to its two edges, two of its borders drawn, its corner rounded
            left = f"calc({c + .5} * var(--cw) - {_LV} / 2)" if hs == "right" else f"calc({c} * var(--cw))"
            top = f"calc({r + .5} * var(--lh) - {_LH} / 2)" if vs == "down" else f"calc({r} * var(--lh))"
            width = f"calc(.5 * var(--cw) + {_LV} / 2)"
            height = f"calc(.5 * var(--lh) + {_LH} / 2)"
            bx = "left" if hs == "right" else "right"
            by = "top" if vs == "down" else "bottom"
            arcs.append(f'<i class="arc" style="--bg:{color};left:{left};top:{top};width:{width};height:{height};'
                        f'border-{bx}:{_LV} solid {color};border-{by}:{_LH} solid {color};'
                        f'border-{by}-{bx}-radius:calc(.5 * var(--cw))"></i>')
    lines: list[list] = []
    for sg in sorted(segs, key=lambda x: (x[0], x[1], x[4], x[5], x[2])):
        m = lines[-1] if lines else None
        if m and m[0] == sg[0] and m[1] == sg[1] and m[4] == sg[4] and m[5] == sg[5] and sg[2] <= m[3] + 1e-9:
            m[3] = max(m[3], sg[3])
        else:
            lines.append(list(sg))
    for kind, fixed, a, b, t, color in lines:
        if kind == "h":
            boxes.append(_box(fixed, a, b - a, 0, color, (0, 0, 0, 0), t, "h"))
        else:
            boxes.append(_box(a, fixed, 0, b - a, color, (0, 0, 0, 0), t, "v"))
    boxes.extend(arcs)
    # the text: as captured, without backgrounds; block characters stay in it (copying), invisible
    text = re.sub(r"background:\s*#[0-9a-fA-F]{3,8};?", "", src)
    text = re.sub(r';(?=")', "", text).replace(' style=""', "")
    drawn = "[\u2580-\u2590\u2594-\u259f" + "".join(_LINES) + "".join(_ARCS) + "]+"
    text = re.sub(drawn, lambda m: f'<span class="bk">{m.group(0)}</span>', text)
    return f'<span class="paint" aria-hidden="true">{"".join(boxes)}</span>{text}'


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit(__doc__)
    sys.stdout.write(render(open(args[0], encoding="utf-8").read(), overlay="--overlay" in sys.argv))
