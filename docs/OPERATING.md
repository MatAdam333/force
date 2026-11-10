# Operating Force

Day-to-day running of a Force server. Install: [README](../README.md#install). Design: [SPEC](SPEC.md).

## Routine

| Task | Command |
|---|---|
| Upgrade Force (developer install) | `git pull && ./deploy.sh` in the repo (tests, new release, atomic switch; sessions untouched) |
| Roll back | `./deploy.sh --rollback` (the 10 newest releases are kept) |
| Health check | `force doctor` (`--fix` repairs layouts, sidebars, options) |
| Self-test | `force smoke --last` (result), `force smoke` (run now, ~1 min) |
| Upgrade Claude Code | automatic: it updates itself; Force restarts idle outdated sessions (`auto_upgrade`) |
| Upgrade OpenCode | `opencodeManaged = true`: automatic (`force settings set opencodeManaged true`, `force opencode-update`, every 6 h); else whoever owns its install |

## Sidebar bottom row

| Shows | Meaning | Do |
|---|---|---|
| `@ host` | normal | — |
| daemon not running | `force.service` is down | `systemctl --user restart force.service` (safe) |
| ✖ self-test failed | the last `force smoke` failed: Claude Code or OpenCode changed in a way Force mishandles | `force smoke --last`; fix or roll back, then `force smoke` |
| a red message | a command failed (shown for a few seconds) | read it; `journalctl --user -u force` |

## A session misbehaves

| Symptom | Do |
|---|---|
| card shows a state you don't believe | `force explain NAME`: which signal decided it, plus the pane's last rows |
| card says down / `the session exited` in the pane | Ctrl+Space x (restart this session) or `force restart NAME` |
| needs you: "trust this folder?" | answer Claude's question in the pane (first start or move into an untrusted folder) |
| crashed 3 times in 10 min, no more restarts | read the pane and `journalctl --user -u force`; restart by hand |
| restart stuck on "restarts after this turn" | the session is still working or waiting on you; finish or answer it |
| sidebar or header missing | `force doctor --fix` |
| everything frozen after a deploy | `./deploy.sh --rollback` |
| OpenCode ignores the first keys | it is still starting (its update dialog is off in Force panes); wait a few seconds |

## Scripts and agents driving sessions (SPEC §43)

| Task | Command |
|---|---|
| Give a session a prompt and wait for the answer | `force prompt NAME "text" --wait --timeout 600` (refuses while it needs you) |
| Wait for a state | `force wait NAME --until your-turn` |
| Read its screen | `force read NAME --lines 120` |
| Answer a dialog | `force keys NAME esc`, `force keys NAME enter`, `force keys NAME --literal 2` |
| Show a job on a shell card | `force report --state working --summary "building"` … `force report --state your-turn --summary "build ok"` |
| Old copies of the session list | `~/.local/share/force/registry-snapshots/` (every 15 min of changes, 48 kept) |

Use these instead of `tmux send-keys`: they find the right pane and do not type into an open approval dialog.

## Never

| Don't | Why |
|---|---|
| `systemctl --user restart force-tmux.service`, `tmux -L force kill-server` | ends every session |
| tests against the `force` socket | tests use `forcetest-*` sockets |
| edit `registry.json` by hand while the daemon runs | use `force rename/move/archive/delete` |

## Where things are

| What | Path |
|---|---|
| sessions (registry), archive copies, releases | `~/.local/share/force/` |
| state: snapshot, usage, smoke results and logs | `~/.local/state/force/` (`smoke/last.json`, `smoke/log/`) |
| config | `~/.config/force/settings.json` (hosts: `~/.config/force/hosts.json`, SPEC §2) |
| logs | `journalctl --user -u force -f`; OpenCode updates: `~/.local/state/force/opencode-update.log` |
| units | `~/.config/systemd/user/force.service`, `force-tmux.service`, `force-smoke.timer` |

## Self-test (`force smoke`)

Starts a real Claude Code and a real OpenCode session on a separate tmux server and checks: start, a turn
(working, then your turn), a permission prompt (needs you), resume after restart, and a move to another
folder (memory kept, new folder's instructions). Uses `haiku` and a few short prompts per run; never
registers for Remote Control; cleans up its transcripts and OpenCode sessions.

| | |
|---|---|
| When | hourly timer, runs only if Claude Code, OpenCode or Force changed, a failure is 6 h old, or once a day |
| Disable | `systemctl --user disable --now force-smoke.timer` (or install with `--no-smoke`) |
| Work folders | `~/.local/state/force/smoke/work/{a,b}` (kept; Claude asks to trust them once) |
