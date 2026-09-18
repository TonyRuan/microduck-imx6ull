#!/bin/sh
# A separate simulator instance so another checkout can keep its own duck running.
set -eu
cd "$(dirname "$0")"
export DUCK_SIM_STATE="${DUCK_SIM_STATE:-$HOME/.cache/duck-keyboard}"
export DUCK_SIM_PORT="${DUCK_SIM_PORT:-7811}"
export DUCK_SIM_FRAME_PORT="${DUCK_SIM_FRAME_PORT:-7911}"
export DUCK_SIM_RL="${DUCK_SIM_RL:-$HOME/Pollen/microduck_rl}"
keyboard_default="$PWD/.keyboard-venv/bin/python"
[ -x "$keyboard_default" ] || keyboard_default="$DUCK_SIM_RL/.venv/bin/python"
keyboard_python="${DUCK_KEYBOARD_PYTHON:-$keyboard_default}"
# Source archive installs use editable packages. Some macOS file managers mark
# their .pth files hidden, which Python skips. Resolve just these two known source
# roots for this process; do not change the shared venv or global Python settings.
source_paths="$("$DUCK_SIM_RL/.venv/bin/python" -c '
import importlib.metadata, json, os
from pathlib import Path
from urllib.parse import unquote, urlparse
paths = [str(Path(os.environ["DUCK_SIM_RL"]) / "src")]
try:
    info = json.loads(importlib.metadata.distribution("better-actuator-models").read_text("direct_url.json") or "{}")
    url = urlparse(info.get("url", ""))
    if info.get("dir_info", {}).get("editable") and url.scheme == "file":
        paths.append(unquote(url.path))
except importlib.metadata.PackageNotFoundError:
    pass
print(os.pathsep.join(paths))
')"
export PYTHONPATH="$source_paths${PYTHONPATH:+:$PYTHONPATH}"
if ! "$keyboard_python" -c 'import tkinter' >/dev/null 2>&1; then
    echo 'Python/Tk unavailable. On macOS: brew install python-tk@3.12'
    exit 1
fi
if ! "$keyboard_python" -c '
import os, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from duck_keyboard import RobotClient
try:
    RobotClient(Path(os.environ["DUCK_SIM_STATE"]) / (os.environ.get("DUCK_SIM_DUCK", "duck-a") + ".sock")).call("robot.health")
except Exception:
    sys.exit(1)
'; then
    scripts/duck-sim
fi
exec scripts/duck-sim keyboard
