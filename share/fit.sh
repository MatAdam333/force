#!/bin/sh
# Keep a cockpit window's fixed panes at their size after tmux resized it proportionally:
# sidebar 29 columns wide (a 7-column rail when the window is narrower than 110 columns, SPEC §25),
# header 2 rows high. Usage: fit.sh <socket_path> <window target>
# Does nothing in a zoomed window (resizing would unzoom it).
S="$1"
W="$2"
t() { tmux -S "$S" "$@"; }
[ "$(t display -p -t "$W" '#{window_zoomed_flag}' 2>/dev/null)" = 1 ] && exit 0
SB=29
[ "$(t display -p -t "$W" '#{window_width}' 2>/dev/null || echo 999)" -lt 110 ] && SB=7
t list-panes -t "$W" -F '#{pane_id} #{@force_role} #{pane_width} #{pane_height}' 2>/dev/null |
while read -r pane role w h; do
  case "$role" in
    sidebar) [ "$w" != "$SB" ] && t resize-pane -t "$pane" -x "$SB" ;;
    header)  [ "$h" != 2 ] && t resize-pane -t "$pane" -y 2 ;;
  esac
done
exit 0
