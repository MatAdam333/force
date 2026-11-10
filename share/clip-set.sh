#!/bin/sh
# Copy stdin to this machine's clipboard (SPEC §41). The ui tmux (split mode) pipes each copy here, so it
# reaches the clipboard even in terminals without OSC 52 (Tilix, GNOME Terminal). Uses the first available of
# wl-copy (Wayland), xclip, xsel or a small GTK holder (clip-hold.py); with no GUI at all, or nothing
# installed, it is a no-op (the terminal still gets the copy via OSC 52 / the copy-command).
set -u

limit=4194304            # 4 MiB: a bigger copy is dropped, not truncated
f=$(mktemp) || exit 0
trap 'rm -f "$f"' EXIT INT TERM
cat > "$f"
[ -s "$f" ] || exit 0                     # empty
[ "$(wc -c < "$f")" -le "$limit" ] || exit 0   # too big

if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then exit 0; fi

if [ -n "${WAYLAND_DISPLAY:-}" ] && command -v wl-copy >/dev/null 2>&1; then
  wl-copy < "$f" >/dev/null 2>&1 || true
  wl-copy --primary < "$f" >/dev/null 2>&1 || true
  exit 0
fi
if [ -n "${DISPLAY:-}" ] && command -v xclip >/dev/null 2>&1; then
  xclip -selection clipboard -i "$f" >/dev/null 2>&1 || true
  xclip -selection primary -i "$f" >/dev/null 2>&1 || true
  exit 0
fi
if [ -n "${DISPLAY:-}" ] && command -v xsel >/dev/null 2>&1; then
  xsel -bi < "$f" >/dev/null 2>&1 || true
  xsel -pi < "$f" >/dev/null 2>&1 || true
  exit 0
fi
if [ -n "${DISPLAY:-}" ] && command -v python3 >/dev/null 2>&1; then
  if [ -n "${FORCE_CLIP_HOLD:-}" ]; then
    sh -c "$FORCE_CLIP_HOLD" < "$f"
  else
    python3 "$(dirname "$0")/clip-hold.py" < "$f"
  fi
  exit 0
fi
exit 0
