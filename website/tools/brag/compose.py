#!/usr/bin/env python3
"""Compose the video/site cockpit screens: the api screen = the new bars over the recorded api agent output
(cockpit-api.ansi, rows 3.. cols 30..), the billing screen = the live capture. The host shows as "devbox"."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import re
import mock_cockpit as mc

PUA = re.compile("([\ue000-\uf8ff\U000f0000-\U000fffff])")


def nf(html):
    """Nerd Font icons each in a one-cell box (class nf, font "Force Symbols"), so the columns stay on the grid."""
    return PUA.sub(r'<span class="nf">\1</span>', html)

def host_as(g, name="devbox", w=29):
    """The capture's host is this machine; show a neutral name, re-centred the way the sidebar centres it
    (model::icon_gap / label_x0: the rule over columns 1..w, a 1- or 2-cell icon gap for even sides)."""
    for r, row in enumerate(g):
        text = "".join(c[0] for c in row[:w])
        if mc.CAPTURE_HOST not in text:
            continue
        rule = row[1][1]
        icon = next(c for c in row[:w] if PUA.match(c[0]))
        label_st = row[text.index(mc.CAPTURE_HOST)][1]
        cells = w - 1
        gap = 2 if (cells - (3 + len(name)) - 2) % 2 == 0 else 1
        label = [[" ", label_st], icon[:]] + [[" ", label_st]] * gap + [[ch, label_st] for ch in name] + [[" ", label_st]]
        x0 = 1 + (cells - len(label)) // 2
        for x in range(1, w):
            row[x] = ["─", rule]
        for i, c in enumerate(label):
            row[x0 + i] = c[:]
    return g

new = mc.parse("cockpit-api-bars.ansi"); old = mc.parse("cockpit-api.ansi")
g = [[c[:] for c in row] for row in new]
for r in range(3, 40):
    for c in range(30, 150):
        g[r][c] = old[r][c][:]
open("cockpit-api-new.html", "w").write(nf(mc.render(host_as(g))))
open("cockpit-billing-new.html", "w").write(nf(mc.render(host_as(mc.parse("cockpit-billing-new.ansi")))))

# the site's second screen: billing-fix asking for permission (its agent pane from cockpit-billing-perm.ansi, a
# later capture of the same session, under the bars of the billing capture)
b = [[c[:] for c in row] for row in mc.parse("cockpit-billing-new.ansi")]
perm = mc.parse("cockpit-billing-perm.ansi")
# (without the demo's own "Remote Control disconnected" notice and the blank line under it: the lines above it move
# down into its place)
agent = [row[30:150] for row in perm[3:40]]
gone = next(i for i, row in enumerate(agent) if "Remote Control disconnected" in "".join(c[0] for c in row))
blank = agent[gone + 1]
agent = [[c[:] for c in blank], [c[:] for c in blank]] + agent[:gone] + agent[gone + 2:]
for r in range(3, 40):
    b[r][30:150] = [c[:] for c in agent[r - 3]]
open("cockpit-billing-perm.html", "w").write(nf(mc.render(host_as(b))))


def short_paths(g, prefix=os.environ.get("FORCE_DEMO_DIR", os.path.expanduser("~/demo-projects")).rstrip("/") + "/", to="~/"):
    """The demo projects live outside the home folder; show them where a user's would be."""
    for row in g:
        text = "".join(c[0] for c in row)
        i = text.find(prefix)
        while i >= 0:
            cut = len(prefix) - len(to)
            for k, ch in enumerate(to):
                row[i + k][0] = ch
            tail = row[i + len(prefix):]
            row[i + len(to):] = [c[:] for c in tail] + [[" ", row[-1][1]] for _ in range(cut)]
            text = "".join(c[0] for c in row)
            i = text.find(prefix)
    return g


# the other five sessions: the agent pane from caps/NAME.ansi (captured while it was in the state the sidebar shows),
# the sidebar and header from caps/NAME-bars.ansi (captured with the snapshot showing NAME as the one you look at)
for name in ["shell", "docs-site", "infra", "mobile", "migration-2024"]:
    path, bars = f"caps/{name}.ansi", f"caps/{name}-bars.ansi"
    if os.path.exists(path):
        g = mc.parse(bars if os.path.exists(bars) else path)
        agent = mc.parse(path)
        for r in range(3, 40):
            g[r][30:150] = [c[:] for c in agent[r][30:150]]
        open(f"cockpit-{name}.html", "w").write(nf(mc.render(host_as(short_paths(g)))))


# Force's menu (Ctrl+Space, or the header's controls button) over each screen: the real tmux menu, captured with
# Force's menu styles on the demo server (caps/menu-NAME.ansi); everything outside its box is left empty, so the
# page lays it over the screen underneath
for name in ["shell", "api", "billing-fix", "docs-site", "infra", "mobile", "migration-2024"]:
    path = f"caps/menu-{name}.ansi"
    if not os.path.exists(path):
        continue
    m = mc.parse(path)
    rows = [r for r, row in enumerate(m) if any(c[0] in "╭╰" for c in row)]
    top, bottom = rows[0], rows[-1]
    left = next(c for c, cell in enumerate(m[top]) if cell[0] == "╭")
    right = next(c for c, cell in enumerate(m[top]) if cell[0] == "╮")
    out = [[[" ", ""] for _ in range(150)] for _ in range(40)]
    for r in range(top, bottom + 1):
        for c in range(left, right + 1):
            out[r][c] = m[r][c][:]
    open(f"menu-{name}.html", "w").write(nf(mc.render(out)))
