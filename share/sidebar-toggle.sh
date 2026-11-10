#!/bin/sh
# Hide / show the cockpit sidebar in every session without restarting anything.
#   hide: move each sidebar pane into its OWN session's background window `force-stash`
#         (break-pane -t "=<session>:"); header and the agent then use the full width.
#   show: join it back as a full-height left column of its session's agent window (join-pane -f).
# Called by the header's `≡ sidebar` button and the menu's "Sidebar on / off" as:
#   sidebar-toggle.sh <socket_path> [<current window id>]
# Toggles run one at a time (flock), so fast on/off/on clicks are applied in order and every run
# converges all sessions to the new state, whatever state a previous run left behind.
S="$1"
CUR="$2"
DIR="$(dirname "$0")"
t() { tmux -S "$S" "$@"; }

exec 9>"${XDG_RUNTIME_DIR:-/tmp}/force-sidebar-toggle.lock"
flock 9

if [ "$(t show -gqv @force_sidebar_hidden)" = 1 ]; then
  t set -gu @force_sidebar_hidden
  want=show
else
  t set -g @force_sidebar_hidden 1
  want=hide
fi

# one line per session: <session> <agent window> <agent pane> <sidebar window> <sidebar pane>
t list-panes -a -F '#{session_name} #{window_id} #{pane_id} #{@force_role}' | awk -v cur="$CUR" '
  $4 == "agent" || $4 == "claude"  { cw[$1] = $2; cp[$1] = $3 }
  $4 == "sidebar" { sw[$1] = $2; sp[$1] = $3 }
  END {
    for (s in cw) if (s in sp) {
      line = s " " cw[s] " " cp[s] " " sw[s] " " sp[s]
      if (cw[s] == cur) first = line; else rest = rest line "\n"
    }
    if (first != "") print first
    printf "%s", rest
  }' |
while read -r sess cwin cpane swin spane; do
  if [ "$want" = hide ] && [ "$swin" = "$cwin" ]; then
    t break-pane -d -s "$spane" -t "=$sess:" -n force-stash
  elif [ "$want" = show ] && [ "$swin" != "$cwin" ]; then
    t join-pane -d -h -b -f -l 29 -s "$spane" -t "$cpane"
    "$DIR/fit.sh" "$S" "$cwin"
  fi
done
