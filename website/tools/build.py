#!/usr/bin/env python3
"""Build the website (useforce.cc) into OUT: index.html with the cockpit screens inlined, the other pages, the
stylesheets, scripts, fonts and media, the Markdown versions for agents, and the files in website/root.

    build.py OUT
"""
import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screen import render   # noqa: E402  (a Force screen as the page draws it)
from md import html_to_md   # noqa: E402  (the docs page as Markdown for agents)

SITE = Path(__file__).resolve().parent.parent
FILES = ["style.css", "site.js", "docs.css", "docs.js", "agent-tools.js"]
PAGES = ["docs.html", "privacy.html"]   # pages copied as they are (index.html is built); both go into sitemap.xml
BASE = "https://useforce.cc"   # the site's address (website/root/CNAME)
REPO = "https://github.com/zalandemeter/force"   # the public repository; set to None to leave every data-repo link out
CONTACT = "hello@useforce.cc"   # the one public address (privacy page, footer, security.txt)
SKILL_DESC = ("Install and drive Force, the code cockpit: start Claude Code, OpenCode and shell sessions that keep "
              "running in tmux, hand them prompts and wait for the answer, read their screens, answer their dialogs, "
              "and report a script's status on its card.")
SKILL_FRONT = f"---\nname: force\ndescription: {json.dumps(SKILL_DESC)}\n---\n\n"   # quoted: the text has colons
SKILL_INSTALL = ("## Install Force (once per machine)\n\n"
                 "If `force` is not on PATH, install it as the user (Linux x86-64, no root):\n"
                 "`curl -fsSL https://useforce.cc/install.sh | sh`. Then `force skill` prints this guide for the\n"
                 "installed version.\n\n")
DIRS = ["fonts", "media"]   # (the cockpit-*.html screens are inlined, not copied)


def repo_links(html: str) -> str:
    """Links marked data-repo point at REPO, or are dropped while there is no public repository (never a dead link)."""
    if REPO:
        return html.replace("{{repo}}", REPO).replace(" data-repo", "")
    html, n = re.subn(r'\n?[ \t]*<a\b[^>]*\bdata-repo\b[^>]*>.*?</a>', "", html, flags=re.S)
    if "{{repo}}" in html:
        raise SystemExit("build: a {{repo}} link is not marked data-repo")
    return html


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    page = (SITE / "index.html").read_text(encoding="utf-8")
    # every <pre ... data-include="FILE"></pre> gets FILE (a Force screen or a fragment of one, as coloured text)
    page, n = re.subn(r'(<pre\b[^>]*?) data-include="([^"]+)"([^>]*)></pre>',
                      lambda m: f'{m.group(1)}{m.group(3)}>{render((SITE / m.group(2)).read_text(encoding="utf-8"), overlay=m.group(2).startswith("menu-"))}</pre>', page)
    if n == 0 or "data-include" in page:
        raise SystemExit("build: an include placeholder is missing or malformed in index.html")
    # versioned links, so a browser never pairs the new page with a stylesheet or script it cached earlier
    for f in FILES:
        v = hashlib.sha1((SITE / f).read_bytes()).hexdigest()[:10]
        page = page.replace(f'"{f}"', f'"{f}?v={v}"')
    # the film and its posters too: a new cut must not meet a cached old one (it has to match the window pixel for pixel)
    page = re.sub(r'"media/([^"?]+)"', lambda m: f'"media/{m.group(1)}?v={hashlib.sha1((SITE / "media" / m.group(1)).read_bytes()).hexdigest()[:10]}"', page)
    (out / "index.html").write_text(repo_links(page), encoding="utf-8")
    for name in PAGES:
        other = (SITE / name).read_text(encoding="utf-8")
        for f in FILES:
            v = hashlib.sha1((SITE / f).read_bytes()).hexdigest()[:10]
            other = other.replace(f'"{f}"', f'"{f}?v={v}"')
        (out / name).write_text(repo_links(other), encoding="utf-8")
    for f in FILES:
        shutil.copy2(SITE / f, out / f)
    for d in DIRS:
        if (out / d).exists():
            shutil.rmtree(out / d)
        shutil.copytree(SITE / d, out / d)
    # Markdown for agents: index.md (written by hand), docs.md (made from docs.html), and llms.txt pointing at
    # both; a Cloudflare Worker (website/worker/markdown.js) serves them for `Accept: text/markdown`
    shutil.copy2(SITE / "index.md", out / "index.md")
    for name in ("docs", "privacy"):
        (out / f"{name}.md").write_text(html_to_md(repo_links((SITE / f"{name}.html").read_text(encoding="utf-8")), f"{name}.html"),
                                        encoding="utf-8")
    (out / "llms.txt").write_text(
        "# Force\n\n"
        "> Force is the code cockpit: it keeps your Claude Code, OpenCode and shell sessions running in tmux on your\n"
        "> machine or a server, through closed terminals and reboots, and shows them all in one terminal with who\n"
        "> needs you. Linux, one-line install: `curl -fsSL https://useforce.cc/install.sh | sh`.\n\n"
        "## Docs\n\n"
        f"- [Overview]({BASE}/index.md): what Force does, the install line, the main commands\n"
        f"- [Documentation]({BASE}/docs.md): install, the cockpit and its states, keys, sessions, other machines,"
        " notifications, scripts and agents (`force prompt`, `wait`, `read`, `keys`, `status`), settings, every command\n"
        f"- [Privacy]({BASE}/privacy.md): what the site and the app process (no cookies, analytics or telemetry)\n"
        "\n## Agent skill\n\n"
        f"- [force]({BASE}/.well-known/agent-skills/force/SKILL.md): install Force and drive its sessions from an agent\n",
        encoding="utf-8")
    # the agent guide (`force skill`, share/skill.md) as an Agent Skill, with a discovery index (Agent Skills
    # Discovery v0.2.0): an agent that finds the site can install Force and drive it
    skill_dir = out / ".well-known" / "agent-skills"
    (skill_dir / "force").mkdir(parents=True, exist_ok=True)
    guide = (SITE.parent / "share" / "skill.md").read_text(encoding="utf-8")
    cut = guide.index("\n## ") + 1                      # the install note goes after the guide's title and intro
    skill = (SKILL_FRONT + guide[:cut] + SKILL_INSTALL + guide[cut:]).encode("utf-8")
    (skill_dir / "force" / "SKILL.md").write_bytes(skill)
    (skill_dir / "index.json").write_text(json.dumps({
        "$schema": "https://schemas.agentskills.io/discovery/0.2.0/schema.json",
        "skills": [{"name": "force", "type": "skill-md", "description": SKILL_DESC,
                    "url": "/.well-known/agent-skills/force/SKILL.md",
                    "digest": "sha256:" + hashlib.sha256(skill).hexdigest()}]}, indent=2) + "\n", encoding="utf-8")
    # security.txt (RFC 9116): where to report a vulnerability; Expires is renewed by every build (a year ahead)
    expires = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")
    (out / ".well-known" / "security.txt").write_text(
        f"Contact: mailto:{CONTACT}\nExpires: {expires}\nPreferred-Languages: en\n"
        f"Canonical: {BASE}/.well-known/security.txt\n", encoding="utf-8")
    # the sitemap lists the built pages, so a page added to PAGES is in it without another step
    urls = [f"{BASE}/"] + [f"{BASE}/{name}" for name in PAGES]
    (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                     '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                     + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n",
                                     encoding="utf-8")
    for f in (SITE / "root").iterdir():
        shutil.copy2(f, out / f.name)
    shutil.copy2(SITE.parent / "LICENSE", out / "LICENSE.txt")   # the licence and its warranty disclaimer, linked from every footer
    (out / ".nojekyll").write_text("")
    print(f"build: {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
