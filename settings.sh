#!/usr/bin/env bash
# Mac / Linux version of Settings.bat: change the quality level or language, then download the model.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${SUBTITLE_MAKER_HOME:-$HOME/.local/share/subtitle-maker}"
VPY="$HOME_DIR/venv/bin/python"
if [ ! -x "$VPY" ]; then
    echo "First time on this computer: run  bash \"$HERE/setup.sh\"  instead."
    exit 1
fi
"$VPY" "$HERE/configure.py"
"$VPY" "$HERE/make_subtitles.py" --prepare
echo
echo "Saved. The next video uses the new settings."
