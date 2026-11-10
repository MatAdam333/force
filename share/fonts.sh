#!/usr/bin/env bash
# Force fonts (SPEC §45): Force's icons and the old terminal kit cleanup.
#
#   force fonts               install the Nerd Font symbols (icons) into ~/.local/share/fonts/force,
#                           then remove the old terminal profiles and launchers
#   force fonts --cleanup-only    remove the old terminal profiles/launchers only (no network)
#   force fonts --uninstall       remove the symbols font AND the old terminal profiles/launchers
#   force fonts -h, --help      this help
set -euo pipefail

BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
FONTS="$HOME/.local/share/fonts/force"
XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CONF_DIR="$XDG_CONFIG_HOME/force"
SYMBOL_FAMILY="Symbols Nerd Font Mono"

# stable ids of the profiles the old kit created (a re-run updates the same profile)
TILIX_UUID=679ebf0c-c4d4-48ca-b4ff-43ea50a1e654
GNOME_UUID=4f0c4ace-f0c3-4c4c-9c4c-f0ce4c000001

NF_URL=https://github.com/ryanoasis/nerd-fonts/releases/download/v3.5.1/NerdFontsSymbolsOnly.tar.xz
NF_SHA=01172f37db8543edb102e5cb5c64101c9f4686630804d49b419aa07b23a69996

say()  { printf '\033[1;38;2;217;119;87mforce\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mforce: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() { cat <<'EOF'
force fonts: install Force's Nerd Font symbols (icons) and remove the old terminal kit.
  force fonts                 install the symbols font into ~/.local/share/fonts/force, then clean up
  force fonts --cleanup-only  only remove the old terminal profiles/launchers (no network)
  force fonts --uninstall     remove the symbols font and the old terminal profiles/launchers
  force fonts -h, --help    this help
EOF
}

CLEANUP_ONLY=0 UNINSTALL=0
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --cleanup-only) CLEANUP_ONLY=1 ;;
  --uninstall) UNINSTALL=1 ;;
  "") ;;
  *) usage >&2; exit 2 ;;
esac

# dconf/gsettings need the session bus. Use the well-known runtime socket when DBUS_SESSION_BUS_ADDRESS is unset;
# without any bus, skip those steps (a silent no-op).
DBUS=0
if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
  DBUS=1
else
  RUNDIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  if [ -S "$RUNDIR/bus" ]; then
    DBUS=1
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNDIR/bus"
  fi
fi

REMOVED=0

rm_any() {  # remove a file or symlink, silent no-op when absent
  local f="$1"
  if [ -e "$f" ] || [ -L "$f" ]; then rm -f "$f"; REMOVED=1; fi
}

# gsettings string-list removal (Tilix / GNOME Terminal profile lists); schema missing -> no-op
list_remove() {
  local schema="$1" id="$2" cur
  cur=$(gsettings get "$schema" list 2>/dev/null) || return 0
  case "$cur" in *"'$id'"*) ;; *) return 0 ;; esac
  cur=$(printf '%s' "$cur" | sed "s/, '$id'//; s/'$id', //; s/'$id'//")
  [ "$cur" = "[]" ] && cur="@as []"
  gsettings set "$schema" list "$cur"
  REMOVED=1
}

# remove the "Force" profile from ~/.config/terminator/config (configobj); missing python3-configobj -> no-op
terminator_profile() {
  local cfg="$XDG_CONFIG_HOME/terminator/config"
  [ -f "$cfg" ] || return 0
  if python3 - "$cfg" <<'PY' 2>/dev/null
import sys
try:
    from configobj import ConfigObj
except ImportError:
    sys.exit(1)
cfg = ConfigObj(sys.argv[1], encoding="utf8")
profiles = cfg.setdefault("profiles", {})
if profiles.pop("Force", None) is not None:
    cfg.write()
    sys.exit(0)
sys.exit(1)
PY
  then
    REMOVED=1
  fi
}

# The old client kit: installed before Force ran locally; its files are gone, but this removes any leftovers.
old_kit_cleanup() {
  if have systemctl; then
    systemctl --user disable --now force-clipd.service >/dev/null 2>&1 && REMOVED=1 || true
  fi
  local f
  for f in "$XDG_CONFIG_HOME/systemd/user/force-clipd.service" \
           "$BIN/force-clipd" "$BIN/force-connect" "$CONF_DIR/client.conf"; do
    rm_any "$f"
  done
  if rmdir "$CONF_DIR" 2>/dev/null; then REMOVED=1; fi
}

cleanup() {  # remove the old terminal kit and profiles; silent no-op for anything absent
  local f
  for f in "$APPS"/force-{kitty,tilix,gnome-terminal,terminator,konsole}.desktop \
           "$XDG_CONFIG_HOME/kitty/force.conf" \
           "$HOME/.local/share/konsole/Force.profile" \
           "$HOME/.local/share/konsole/Force.colorscheme" \
           "$HOME/.local/share/konsole/Force.keytab"; do
    rm_any "$f"
  done
  if [ "$DBUS" = 1 ] && have dconf; then
    local dir
    for dir in "/com/gexperts/Tilix/profiles/$TILIX_UUID/" "/org/gnome/terminal/legacy/profiles:/:$GNOME_UUID/"; do
      # dconf reset exits 0 for an absent path too: only count it when the profile had keys
      if [ -n "$(dconf dump "$dir" 2>/dev/null)" ]; then
        dconf reset -f "$dir" >/dev/null 2>&1 && REMOVED=1 || true
      fi
    done
  fi
  if [ "$DBUS" = 1 ] && have gsettings; then
    list_remove com.gexperts.Tilix.ProfilesList "$TILIX_UUID"
    list_remove org.gnome.Terminal.ProfilesList "$GNOME_UUID"
  fi
  terminator_profile
  old_kit_cleanup
  if [ "$REMOVED" = 1 ] && have update-desktop-database; then
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
  fi
  if [ "$REMOVED" = 1 ]; then
    say "removed the old terminal profiles and launchers"
  fi
}

# --- fonts ----------------------------------------------------------------------------------------------
fetch() {  # fetch <url> <sha256> <dest>
  local tmp; tmp=$(mktemp)
  curl -fsSL --retry 2 -o "$tmp" "$1" || { rm -f "$tmp"; die "download failed: $1"; }
  echo "$2  $tmp" | sha256sum -c --quiet - || { rm -f "$tmp"; die "checksum mismatch: $1"; }
  mv "$tmp" "$3"
}
# (no grep -q: it quits early, fc-list gets SIGPIPE, and pipefail turns the match into a failure)
has_family() { fc-list : family 2>/dev/null | tr ',' '\n' | grep -xF "$1" >/dev/null; }
install_font() {
  have fc-list || die "fontconfig missing: install fontconfig (fc-list) to add the icon font"
  mkdir -p "$FONTS"
  if has_family "$SYMBOL_FAMILY"; then
    say "fonts: Nerd Font symbols already installed"
    return
  fi
  say "fonts: installing the Nerd Font symbols (icons)"
  local tmp; tmp=$(mktemp -d)
  fetch "$NF_URL" "$NF_SHA" "$tmp/nf.tar.xz"
  tar -xJf "$tmp/nf.tar.xz" -C "$tmp" SymbolsNerdFontMono-Regular.ttf SymbolsNerdFont-Regular.ttf LICENSE
  mv "$tmp/SymbolsNerdFontMono-Regular.ttf" "$tmp/SymbolsNerdFont-Regular.ttf" "$FONTS/"
  mv "$tmp/LICENSE" "$FONTS/LICENSE-NerdFontsSymbols"
  rm -rf "$tmp"
  fc-cache -f "$FONTS" >/dev/null 2>&1 || true
  say "fonts: installed the Nerd Font symbols (icons)"
}

# --- main -----------------------------------------------------------------------------------------------
if [ "$CLEANUP_ONLY" = 1 ]; then
  cleanup
  exit 0
fi
if [ "$UNINSTALL" = 1 ]; then
  cleanup
  if [ -e "$FONTS" ] || [ -L "$FONTS" ]; then rm -rf "$FONTS"; REMOVED=1; fi
  have fc-cache && fc-cache -f "$(dirname "$FONTS")" >/dev/null 2>&1 || true
  exit 0
fi
install_font
cleanup
