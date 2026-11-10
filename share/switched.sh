#!/bin/sh
# client-session-changed / client-attached hook: refit the panes, record the glyph tier of the client's
# terminal on the session (SPEC §28), then signal the newly shown session's sidebar and header so they
# redraw right away (sidebar: arrival fade; both: re-read the tier).
# Usage: switched.sh <socket_path> <new session> <previous session or ""> [<client name>]
# Split mode (SPEC §39): the client may be a ui server's nested client; its ui window's sidebar and header
# (whose @force_nested is that client) are signalled too, and told the new session.
S="$1"
NEW="$2"
PREV="$3"
CLIENT="$4"
t() { tmux -S "$S" "$@"; }
D=$(dirname "$0")
UI=$(tmux -S "$S" show -gqv @force_ui_socket 2>/dev/null)
if [ -n "$UI" ] && [ -n "$CLIENT" ] && tmux -L "$UI" has-session 2>/dev/null; then
  # Only panes whose process is the Rust program get USR1: a sidebar or header still starting (the `force`
  # launcher before it execs) would be killed by it (USR1's default action), and reads everything at start anyway.
  tmux -L "$UI" list-panes -a -F '#{@force_nested}	#{window_id}	#{?#{m:force-*,#{pane_current_command}},#{@force_role},starting}	#{pane_pid}' 2>/dev/null |
  awk -F '\t' -v c="$CLIENT" '$1 == c' |
  while IFS='	' read -r _n win role pid; do
    tmux -L "$UI" set-option -w -t "$win" @force_session "$NEW" 2>/dev/null
    case "$role" in sidebar|header) kill -USR1 "$pid" 2>/dev/null ;; esac
  done
fi
"$D/fit.sh" "$S" "$NEW:"
# The tier of the shown session (all its clients' terminals), and of the one the client left.
LEFT="$PREV"
"$D/tier.sh" "$S" "$NEW" && PREV=""   # tier changed: signal the panes even without a switch
[ -n "$LEFT" ] && [ "$LEFT" != "$NEW" ] && "$D/tier.sh" "$S" "$LEFT" --signal
[ "$PREV" = "$NEW" ] && exit 0
t list-panes -s -t "=$NEW" -F '#{?#{m:force-*,#{pane_current_command}},#{@force_role},starting} #{pane_pid}' 2>/dev/null |
while read -r role pid; do
  case "$role" in sidebar|header) kill -USR1 "$pid" 2>/dev/null ;; esac
done
exit 0
