#!/usr/bin/env bash
# Mac / Linux: run once on each new computer (bash setup.sh).
# Installs the packages and models outside the Tools folder, in ~/.local/share/subtitle-maker.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${SUBTITLE_MAKER_HOME:-$HOME/.local/share/subtitle-maker}"
echo "Setting up the subtitle maker in $HOME_DIR"

PY=""
for v in python3.12 python3.11 python3.10 python3; do
    if command -v "$v" >/dev/null 2>&1 &&
       "$v" -c 'import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 12))'; then
        PY="$v"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.10-3.12 not found."
    echo "Mac: install from https://www.python.org/downloads/ or 'brew install python@3.12'."
    echo "Linux: install python3.12 and python3.12-venv with your package manager."
    exit 1
fi
echo "Using $PY"

[ -x "$HOME_DIR/venv/bin/python" ] || "$PY" -m venv "$HOME_DIR/venv"
VPY="$HOME_DIR/venv/bin/python"
echo "Installing packages (a few minutes the first time)..."
"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -r "$HERE/requirements.txt"

# NVIDIA GPU support (Linux only; Macs run on the CPU)
if [ "$(uname)" = "Linux" ] && command -v nvidia-smi >/dev/null 2>&1; then
    echo "NVIDIA GPU found, installing GPU support (about 1 GB)..."
    "$VPY" -m pip install -q -r "$HERE/requirements-gpu.txt"
fi

echo
"$VPY" "$HERE/configure.py"
"$VPY" "$HERE/make_subtitles.py" --prepare
echo
echo "All set. Open the window:  bash \"$HERE/app.sh\""
echo "Or from the terminal:     bash \"$HERE/subtitles.sh\" \"your video.mp4\""
echo "To change the quality level or language later:  bash \"$HERE/settings.sh\""
