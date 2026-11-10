#!/bin/sh
# client-resized / client-attached / client-session-changed hook: give every cockpit window that no client is
# showing the size of this client's window and the glyph tier of its terminal, so a later switch needs no
# resize and no tier flip. A resize at switch time makes Claude Code redraw its whole screen, and a tier
# flip redraws the sidebar and header a moment after the switch: both look like the session reloading
# (SPEC §26, §28). Hidden panes whose tier changed are signalled to redraw at once, unseen.
# Usage: presize.sh <socket_path> <client name>
# Debounced: a drag-resize fires many hooks; only the last one within 0.3 s does the work.
S="$1"
C="$2"
t() { tmux -S "$S" "$@"; }
TOKEN="${XDG_STATE_HOME:-$HOME/.local/state}/force/presize.token"
echo "$$" > "$TOKEN" 2>/dev/null || exit 0
sleep 0.3
[ "$(cat "$TOKEN" 2>/dev/null)" = "$$" ] || exit 0

size=$(t display -p -c "$C" '#{window_width} #{window_height}' 2>/dev/null) || exit 0
set -- $size
W="$1"
H="$2"
[ -n "$W" ] && [ -n "$H" ] || exit 0
shown=" $(t list-clients -F '#{window_id}' 2>/dev/null | tr '\n' ' ') "

# this client's tier (same rule as tier.sh) for the sessions nobody is looking at
term=$(t display -p -c "$C" '#{client_termname} #{client_termtype}' 2>/dev/null)
rich=$(t show -gqv @force_rich_terms 2>/dev/null)
[ -n "$rich" ] || rich='kitty|ghostty|wezterm'
stated=" $(t show -gqv @force_client_glyphs 2>/dev/null) "
case "$stated" in
  *" $C=rich "*) tier=rich ;;
  *" $C=plain "*) tier=plain ;;
  *) if printf '%s\n' "$term" | grep -q -i -E "$rich"; then tier=rich; else tier=plain; fi ;;
esac
viewed=" $(t list-clients -F '#{client_session}' 2>/dev/null | tr '\n' ' ') "
t list-sessions -F '#{session_name}	#{@force_glyphs}' 2>/dev/null |
while IFS='	' read -r sess cur; do
  case "$viewed" in *" $sess "*) continue ;; esac
  [ "$cur" = "$tier" ] && continue
  t set-option -t "=$sess:" @force_glyphs "$tier" 2>/dev/null
  # redraw the hidden sidebar and header now, not on their next snapshot read
  # Only panes whose process is the Rust program get USR1: a sidebar or header still starting (the `force`
  # launcher before it execs) would be killed by it (USR1's default action), and reads everything at start anyway.
  t list-panes -s -t "=$sess" -F '#{?#{m:force-*,#{pane_current_command}},#{@force_role},starting} #{pane_pid}' 2>/dev/null |
  while read -r role pid; do
    case "$role" in sidebar|header) kill -USR1 "$pid" 2>/dev/null ;; esac
  done
done

t list-windows -a -F '#{window_id} #{window_width} #{window_height}' 2>/dev/null |
while read -r win w h; do
  case "$shown" in *" $win "*) continue ;; esac
  [ "$w" = "$W" ] && [ "$h" = "$H" ] && continue
  # resize-window pins the window to `manual`; unset it again so the global `smallest` rule still applies.
  t resize-window -t "$win" -x "$W" -y "$H" 2>/dev/null
  t set-option -w -u -t "$win" window-size 2>/dev/null
  "$(dirname "$0")/fit.sh" "$S" "$win"
done
exit 0
