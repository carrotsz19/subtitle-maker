#!/usr/bin/env bash
# Mac / Linux version of the drag-and-drop .bat files.
#   bash subtitles.sh "video.mp4"                  one speaker
#   bash subtitles.sh "video.mp4" --speakers 2     detect who is talking (0 = guess)
HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${SUBTITLE_MAKER_HOME:-$HOME/.local/share/subtitle-maker}"
VPY="$HOME_DIR/venv/bin/python"
if [ ! -x "$VPY" ]; then
    echo "First time on this computer: run  bash \"$HERE/setup.sh\"  first."
    exit 1
fi

# On Linux, let Whisper find the NVIDIA libraries installed by setup.sh
NV_LIBS="$("$VPY" -c 'import glob, os, nvidia; print(":".join(glob.glob(os.path.join(list(nvidia.__path__)[0], "*", "lib"))))' 2>/dev/null)"
[ -n "$NV_LIBS" ] && export LD_LIBRARY_PATH="$NV_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "$VPY" "$HERE/make_subtitles.py" "$@"
