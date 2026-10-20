#!/bin/bash
# One-command Mac setup for Letterbox Desk. Safe to run again.
# Usage: ./install-mac.sh [--letterbox-dir DIR] [--helper PATH] [--foreground]
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
state="$HOME/.bell-desk"
letterbox_dir=""
helper=""
foreground=false
while [ $# -gt 0 ]; do
  case "$1" in
    --letterbox-dir) letterbox_dir="${2:?--letterbox-dir needs a folder}"; shift ;;
    --helper) helper="${2:?--helper needs a path}"; shift ;;
    --foreground) foreground=true ;;
    *) echo "usage: $0 [--letterbox-dir DIR] [--helper PATH] [--foreground]" >&2; exit 2 ;;
  esac
  shift
done
step() { printf '\n▸ %s\n' "$1"; }
bell() { "$here/mac/.venv/bin/bell-desk" "$@"; }

step "Installing Letterbox Desk"
cd "$here/mac"
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null; then
    uv venv -q --python ">=3.11" .venv
  elif command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    python3 -m venv .venv
  else
    echo "  Letterbox Desk needs Python 3.11 or newer. Install uv (https://docs.astral.sh/uv/) and run this again." >&2
    exit 1
  fi
fi
if command -v uv >/dev/null; then
  uv pip install -q --python .venv/bin/python -e .
else
  .venv/bin/python -m pip install -q -e .
fi
if "$here/mac/Tools/build-window.sh" 2>/dev/null; then
  rm -f build/window-unavailable
  echo "  Mac window bundle built"
else
  mkdir -p build
  touch build/window-unavailable
  echo "  Mac windows not built; plain pop-ups will be used"
fi

step "Connecting to your letterbox"
if [ -z "$letterbox_dir$helper" ] && [ -f "$state/config.json" ] && grep -q helper_sha256 "$state/config.json"; then
  echo "  already set up, left as it is (pass --letterbox-dir or --helper to set it up again)"
else
  args=()
  [ -n "$letterbox_dir" ] && args+=(--letterbox-dir "$letterbox_dir")
  [ -n "$helper" ] && args+=(--helper "$helper")
  bell setup ${args[@]+"${args[@]}"}
fi

step "Pictures"
"$here/mac/Tools/setup-pictures.sh" "$here/mac/.venv/bin/bell-desk"

step "Starting the listener"
if pgrep -f 'bell-desk.* serve' >/dev/null; then
  echo "  already running. If you changed the setup, quit it (type quit in its window) and run this again."
elif $foreground; then
  echo "  Run this in a terminal and keep it open:  $here/mac/.venv/bin/bell-desk serve"
else
  read -r -p "  Start Letterbox Desk now and every time you log in? [Y/n] " answer
  case "${answer:-y}" in
    [Yy]*) if ! bell service install; then echo "  Letterbox Desk did not start. Fix the problem above, then run: $here/mac/.venv/bin/bell-desk service install" >&2; exit 1; fi ;;
    *) echo "  Not started. Start it any time with:  $here/mac/.venv/bin/bell-desk service install" ;;
  esac
fi

printf '\nDone. Letterbox Desk is ready. If you enabled iPhone (Letterbox Bell), open Bell on the phone to pair.\n'
