#!/usr/bin/env bash
# Developer install of Force from this checkout. Users install with the one-line installer instead
# (curl -fsSL https://useforce.cc/install.sh | sh), which needs nothing on the machine.
#
#   ./install.sh [setup options: --no-fonts --no-services --self-test]
#
# Needs Python 3.11+, tmux, git and Rust (https://rustup.rs). Creates the test venv
# (~/.local/share/force/venv, pytest), deploys this working tree (./deploy.sh: tests, build, release), then runs
# Force's setup on that release: the `force` command, PATH, the services, the icon font.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
DATA="${FORCE_HOME:-$HOME/.local/share/force}"

PY=""
for c in python3 python3.13 python3.12 python3.11; do
  p="$(command -v "$c" 2>/dev/null || true)"
  [[ -n "$p" ]] && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null && { PY="$p"; break; }
done
[[ -n "$PY" ]] || { echo "install: needs Python 3.11+ (the one-line installer brings its own)" >&2; exit 1; }
command -v tmux >/dev/null || { echo "install: needs tmux" >&2; exit 1; }
command -v cargo >/dev/null || [[ -x "$HOME/.cargo/bin/cargo" ]] \
  || { echo "install: needs Rust: curl --proto '=https' -sSf https://sh.rustup.rs | sh" >&2; exit 1; }

VENV="$DATA/venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "install: creating the test venv $VENV"
  "$PY" -m venv "$VENV" && "$VENV/bin/python" -m pip install -q pytest
fi

"$REPO/deploy.sh"
REL="$(readlink -f "$DATA/app")"
exec "$PY" -I -c 'import runpy, sys; sys.path.insert(0, sys.argv[1]); sys.argv = ["force setup"] + sys.argv[2:]; runpy.run_module("force.setup", run_name="__main__")' \
  "$REL" --release "$REL" --python "$PY" "$@"
