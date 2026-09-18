#!/bin/sh
# The panel owns its simulator and selected control backend.
set -eu
cd "$(dirname "$0")"
export DUCK_SIM_RL="${DUCK_SIM_RL:-$(cd .. && pwd)/microduck_rl}"
keyboard_python="${DUCK_KEYBOARD_PYTHON:-$PWD/.keyboard-venv/bin/python}"
if [ ! -x "$keyboard_python" ]; then
    keyboard_python="$(command -v python3.12 || command -v python3)"
fi
if ! "$keyboard_python" -c 'import tkinter; import AppKit' >/dev/null 2>&1; then
    echo 'GUI dependencies missing. See docs/robot/simulation.md (keyboard setup).'
    exit 1
fi
exec "$keyboard_python" scripts/duck_keyboard.py "$@"
