#!/usr/bin/env python3
"""Cut a rectangle of cells out of a cockpit screen (cockpit-NAME.html: one line per terminal row, coloured spans)
and write it as a fragment the page includes like the screens themselves (<pre data-include="crop-NAME.html">).

    crop.py SCREEN ROW0 ROW1 COL0 COL1 OUT      (rows and columns inclusive, 0-based)

Every character is one terminal cell, the Nerd Font icons (<span class="nf">) included, so the cut keeps the grid.
"""
import html
import sys
from html.parser import HTMLParser
from pathlib import Path


class Cells(HTMLParser):
    """One row: a list of (char, style, nf) per cell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.cells: list[tuple[str, str, bool]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        style = a.get("style") or (self.stack[-1][0] if self.stack else "")
        self.stack.append((style, a.get("class") == "nf"))

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        style = self.stack[-1][0] if self.stack else ""
        nf = any(n for _, n in self.stack)
        self.cells.extend((ch, style, nf) for ch in data)


def row_cells(line: str) -> list[tuple[str, str, bool]]:
    p = Cells()
    p.feed(line)
    return p.cells


def render(cells: list[tuple[str, str, bool]]) -> str:
    out, i = [], 0
    while i < len(cells):
        ch, style, nf = cells[i]
        j = i
        while j < len(cells) and cells[j][1] == style and cells[j][2] == nf and not nf:
            j += 1
        if nf:
            j = i + 1
        text = html.escape("".join(c[0] for c in cells[i:j]), quote=False)
        inner = f'<span class="nf">{text}</span>' if nf else text
        out.append(f'<span style="{style}">{inner}</span>' if style else inner)
        i = j
    return "".join(out)


def main(screen: Path, r0: int, r1: int, c0: int, c1: int, out: Path) -> None:
    lines = screen.read_text(encoding="utf-8").split("\n")
    rows = [row_cells(lines[r])[c0:c1 + 1] for r in range(r0, r1 + 1)]
    out.write_text("\n".join(render(r) for r in rows), encoding="utf-8")
    print(f"crop: {out} ({r1 - r0 + 1} rows x {c1 - c0 + 1} cells)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) != 6:
        raise SystemExit(__doc__)
    main(Path(a[0]), int(a[1]), int(a[2]), int(a[3]), int(a[4]), Path(a[5]))
