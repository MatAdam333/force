#!/usr/bin/env python3
"""Terminal screen (tmux `capture-pane -e -p`) -> an HTML <pre> of coloured spans, for the landing page's cockpit
picture: real text, sharp at any zoom, selectable. Handles the SGR codes tmux emits: reset, bold, dim, italic,
underline, reverse, 16/256/true colours (fg and bg).

    ansi2html.py IN.ansi OUT.html [--sub OLD=NEW ...]

--sub replaces text in the captured screen (keeping its width), e.g. the staging machine's host name.
"""
import html
import re
import sys

DEFAULT_FG, DEFAULT_BG = "#d0d0d0", "#262626"
BASE16 = ["#000000", "#cc0403", "#19cb00", "#cecb00", "#0d73cc", "#cb1ed1", "#0dcdcd", "#dddddd",
          "#767676", "#f2201f", "#23fd00", "#fffd00", "#1a8fff", "#fd28ff", "#14ffff", "#ffffff"]   # Force's palette


def xterm256(n: int) -> str:
    if n < 16:
        return BASE16[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (steps[n // 36], steps[(n // 6) % 6], steps[n % 6])
    v = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (v, v, v)


class Style:
    def __init__(self):
        self.reset()

    def reset(self):
        self.fg = self.bg = None
        self.bold = self.dim = self.italic = self.under = self.rev = False

    def key(self):
        return (self.fg, self.bg, self.bold, self.dim, self.italic, self.under, self.rev)

    def css(self) -> str:
        fg, bg = self.fg or DEFAULT_FG, self.bg
        if self.rev:
            fg, bg = (self.bg or DEFAULT_BG), (self.fg or DEFAULT_FG)
        out = []
        if fg != DEFAULT_FG:
            out.append(f"color:{fg}")
        if bg and bg != DEFAULT_BG:
            out.append(f"background:{bg}")
        if self.bold:
            out.append("font-weight:700")
        if self.dim:
            out.append("opacity:.62")
        if self.italic:
            out.append("font-style:italic")
        if self.under:
            out.append("text-decoration:underline")
        return ";".join(out)

    def apply(self, params: list[int]):
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self.reset()
            elif p == 1:
                self.bold = True
            elif p == 2:
                self.dim = True
            elif p == 3:
                self.italic = True
            elif p == 4:
                self.under = True
            elif p == 7:
                self.rev = True
            elif p == 22:
                self.bold = self.dim = False
            elif p == 23:
                self.italic = False
            elif p == 24:
                self.under = False
            elif p == 27:
                self.rev = False
            elif 30 <= p <= 37:
                self.fg = BASE16[p - 30]
            elif 90 <= p <= 97:
                self.fg = BASE16[p - 90 + 8]
            elif 40 <= p <= 47:
                self.bg = BASE16[p - 40]
            elif 100 <= p <= 107:
                self.bg = BASE16[p - 100 + 8]
            elif p == 39:
                self.fg = None
            elif p == 49:
                self.bg = None
            elif p in (38, 48) and i + 1 < len(params):
                if params[i + 1] == 5 and i + 2 < len(params):
                    col = xterm256(params[i + 2])
                    i += 2
                elif params[i + 1] == 2 and i + 4 < len(params):
                    col = "#%02x%02x%02x" % tuple(params[i + 2:i + 5])
                    i += 4
                else:
                    col = None
                if p == 38:
                    self.fg = col
                else:
                    self.bg = col
            i += 1


SGR = re.compile(r"\x1b\[([0-9;:]*)m")
OTHER_ESC = re.compile(r"\x1b(\[[0-9;:?]*[A-Za-ln-z]|\][^\x07\x1b]*(\x07|\x1b\\)|[()][0-9A-Za-z])")
# symbols the page's monospace font (DejaVu Sans Mono) lacks -> one-cell look-alikes it has, so the grid holds
CHAR_MAP = str.maketrans({"\u23bf": "\u2514", "\u23f5": "\u25b6",
                          **{chr(c): "\u25d0" for c in range(0x2800, 0x2900)}})


def convert(text: str) -> str:
    style = Style()
    lines_out = []
    for line in text.split("\n"):
        parts, pos, cur_key, buf = [], 0, None, []

        def flush():
            if buf:
                css = style_css[0]
                chunk = html.escape("".join(buf))
                parts.append(f'<span style="{css}">{chunk}</span>' if css else chunk)
                buf.clear()

        style_css = [style.css()]
        cur_key = style.key()
        for m in SGR.finditer(line):
            buf.append(line[pos:m.start()])
            params = [int(x) if x else 0 for x in re.split("[;:]", m.group(1))] if m.group(1) else [0]
            style.apply(params)
            if style.key() != cur_key:
                flush()
                cur_key = style.key()
                style_css[0] = style.css()
            pos = m.end()
        buf.append(line[pos:])
        flush()
        lines_out.append("".join(parts))
    while lines_out and not re.sub(r"<[^>]+>", "", lines_out[-1]).strip():
        lines_out.pop()
    return "\n".join(lines_out)


def main(argv):
    src, dst, subs = argv[0], argv[1], []
    rest = argv[2:]
    while rest:
        if rest[0] == "--sub" and len(rest) > 1:
            old, new = rest[1].split("=", 1)
            subs.append((old, new))
            rest = rest[2:]
        else:
            raise SystemExit(f"unknown argument {rest[0]}")
    text = OTHER_ESC.sub("", open(src, encoding="utf-8").read()).translate(CHAR_MAP)
    for old, new in subs:
        # keep the screen's columns: pad or cut the replacement to the original's width
        text = text.replace(old, new.ljust(len(old))[:max(len(old), len(new))] if len(new) <= len(old) else new)
    open(dst, "w", encoding="utf-8").write(convert(text))


if __name__ == "__main__":
    main(sys.argv[1:])
