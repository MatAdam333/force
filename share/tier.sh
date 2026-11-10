#!/bin/sh
# Glyph tier of one session (SPEC §28): `rich` only when every client showing it runs a terminal that
# draws Nerd Font icons: the tier the client stated (`force --glyphs`, @force_client_glyphs), else the
# config's `rich_terminals` regex (published as @force_rich_terms; default kitty / ghostty / wezterm) against
# "termname termtype"; a session seen in a rich and a plain terminal at once gets `plain`, since tmux draws
# one picture for all its clients. No client: the tier is left as it is.
# Usage: tier.sh <socket_path> <session> [--signal]
# Without --signal: exit 0 when the tier changed, 1 when not (switched.sh uses it).
# With --signal (hooks): the session's sidebar and header are told to redraw on a change, and the exit status
# is always 0, because tmux shows a failing run-shell hook's status over the window ("returned 1").
S="$1"
SESS="$2"
SIG="$3"
no_change() { [ "$SIG" = "--signal" ] && exit 0; exit 1; }
t() { tmux -S "$S" "$@"; }
[ -n "$SESS" ] || no_change
rich=$(t show -gqv @force_rich_terms 2>/dev/null)
[ -n "$rich" ] || rich='kitty|ghostty|wezterm'
# a client that stated its tier (`force --glyphs rich|plain`, @force_client_glyphs "<tty>=<tier>") counts as
# that; any other client by its terminal type
stated=$(t show -gqv @force_client_glyphs 2>/dev/null)
tier=$(t list-clients -F '#{client_session}	#{client_name}	#{client_termname} #{client_termtype}' 2>/dev/null |
  awk -F '\t' -v s="$SESS" -v rich="$rich" -v stated="$stated" '
    BEGIN { n = split(stated, w, " "); for (i = 1; i <= n; i++) { split(w[i], kv, "="); st[kv[1]] = kv[2] }
            t = "rich"; seen = 0 }
    $1 == s { seen = 1
              if ($2 in st) v = st[$2]; else v = (tolower($3) ~ tolower(rich)) ? "rich" : "plain"
              if (v != "rich") t = "plain" }
    END { if (seen) print t }')
[ -n "$tier" ] || no_change
[ "$(t show -qv -t "=$SESS:" @force_glyphs 2>/dev/null)" = "$tier" ] && no_change
t set-option -t "=$SESS:" @force_glyphs "$tier" 2>/dev/null || no_change
if [ "$SIG" = "--signal" ]; then
  # Only panes whose process is the Rust program get USR1: a sidebar or header still starting (the `force`
  # launcher before it execs) would be killed by it (USR1's default action), and reads everything at start anyway.
  t list-panes -s -t "=$SESS" -F '#{?#{m:force-*,#{pane_current_command}},#{@force_role},starting} #{pane_pid}' 2>/dev/null |
  while read -r role pid; do
    case "$role" in sidebar|header) kill -USR1 "$pid" 2>/dev/null ;; esac
  done
fi
exit 0
