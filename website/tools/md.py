"""A page (docs, privacy) as Markdown for agents (docs.md): the text of <main class="doc"> converted, so it never drifts
from docs.html. Headings, paragraphs, list items, code blocks, the install line, and tables; inline code, keys, bold and
links. Tables with a header row stay tables; two-column reference tables become lists (`- **key**: text`)."""
from __future__ import annotations

import re
from html.parser import HTMLParser

BASE = "https://useforce.cc"


class _Doc(HTMLParser):
    def __init__(self, page: str):
        super().__init__(convert_charrefs=True)
        self.page = page
        self.out: list[str] = []          # finished blocks
        self.buf: list[str] = []          # the current inline text
        self.depth_main = 0               # inside <main class="doc">
        self.skip = 0                     # inside nav / button / script / style
        self.pre = False
        self.pre_text: list[str] = []
        self.heading = 0
        self.table: list[list[str]] | None = None
        self.table_head = False
        self.row: list[str] | None = None
        self.cell = False
        self.href: str | None = None
        self.install = False
        self.li = False                   # inside <li>: the text becomes a list item

    # inline text goes to the current cell, else the paragraph buffer
    def _emit(self, s: str) -> None:
        if self.cell and self.row is not None:
            self.row[-1] += s
        else:
            self.buf.append(s)

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if text:
            self.out.append(("#" * self.heading + " " if self.heading else "- " if self.li else "") + text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "main" and "doc" in cls.split():
            self.depth_main += 1
            return
        if not self.depth_main:
            return
        if tag in ("nav", "button", "script", "style"):
            self.skip += 1
        if self.skip:
            return
        if tag == "div" and "install" in cls.split():
            self._flush()
            self.install = True
            self.pre_text = []
        elif tag in ("h1", "h2", "h3"):
            self._flush()
            self.heading = int(tag[1])
        elif tag == "p":
            self._flush()
        elif tag == "li":
            self._flush()
            self.li = True
        elif tag == "pre":
            self._flush()
            self.pre, self.pre_text = True, []
        elif tag == "table":
            self._flush()
            self.table, self.table_head = [], False
        elif tag == "thead":
            self.table_head = True
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("th", "td") and self.row is not None:
            self.row.append("")
            self.cell = True
        elif tag in ("code", "kbd") and not self.pre and not self.install:
            self._emit("`")
        elif tag == "strong":
            self._emit("**")
        elif tag == "a":
            href = a.get("href") or ""
            if href.startswith("#"):
                href = f"{BASE}/{self.page}{href}"
            elif href and not re.match(r"[a-z][a-z0-9+.-]*:", href):   # relative (http:, mailto: stay as they are)
                href = f"{BASE}/{href.lstrip('./')}" if href not in ("./", "") else f"{BASE}/"
            self.href = href
            self._emit("[")

    def handle_endtag(self, tag):
        if not self.depth_main:
            return
        if tag == "main":
            self._flush()
            self.depth_main -= 1
            return
        if tag in ("nav", "button", "script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "div" and self.install:
            line = re.sub(r"\s+", " ", "".join(self.pre_text)).strip().lstrip("$").strip()
            self.out.append(f"```sh\n{line}\n```")
            self.install = False
        elif tag in ("h1", "h2", "h3"):
            self._flush()
            self.heading = 0
        elif tag == "p":
            self._flush()
        elif tag == "li":
            self._flush()
            self.li = False
        elif tag == "pre":
            self.pre = False
            body = "".join(self.pre_text).strip("\n")
            body = "\n".join(l[2:] if l.startswith("$ ") else l for l in body.split("\n"))   # no prompt signs
            self.out.append(f"```sh\n{body}\n```")
        elif tag in ("th", "td"):
            self.cell = False
        elif tag == "tr" and self.row is not None and self.table is not None:
            self.table.append([re.sub(r"\s+", " ", c).strip() for c in self.row])
            self.row = None
        elif tag == "table" and self.table is not None:
            rows, self.table = self.table, None
            if self.table_head and rows:
                head, body = rows[0], rows[1:]
                lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                lines += ["| " + " | ".join(r) + " |" for r in body]
            else:
                lines = [f"- **{r[0]}**: {' '.join(r[1:])}" if len(r) > 1 else f"- {r[0]}" for r in rows]
            self.out.append("\n".join(lines))
        elif tag in ("code", "kbd") and not self.pre and not self.install:
            self._emit("`")
        elif tag == "strong":
            self._emit("**")
        elif tag == "a" and self.href is not None:
            self._emit(f"]({self.href})")
            self.href = None

    def handle_data(self, data):
        if not self.depth_main or self.skip:
            return
        if self.pre or self.install:
            self.pre_text.append(data)
        else:
            self._emit(data)


def html_to_md(html: str, page: str) -> str:
    p = _Doc(page)
    p.feed(html)
    p.close()
    text = "\n\n".join(p.out).replace("**`", "`").replace("`**", "`")   # bold around code: the code is enough
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
